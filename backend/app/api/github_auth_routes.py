"""Browser-bound GitHub authentication and post-signup connector continuation."""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import authenticated_user, organization_principal
from app.api.auth_routes import _payload
from app.api.product_common import require_permission
from app.application.services.github_connector import connect_installation
from app.application.services.integration_credentials import decode_bundle, encode_bundle
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.native.http import ProviderTransportError
from app.infrastructure.auth.github import (
    check_flow,
    digest,
    pkce_challenge,
    resolve_identity,
    verified_email,
)
from app.infrastructure.auth.service import AuthenticatedUser, RegistrationFailure
from app.infrastructure.auth.session_store import RedisSessionStore
from app.infrastructure.database.models import (
    GitHubAuthFlow,
    GitHubInstallationBinding,
    GitHubOnboarding,
    IntegrationConnectionState,
)
from app.infrastructure.database.session import database_session
from app.infrastructure.secrets.integration_crypto import (
    decrypt_integration_secret,
    encrypt_integration_secret,
)

router = APIRouter(tags=["github-auth"])
Session = Annotated[AsyncSession, Depends(database_session)]
User = Annotated[AuthenticatedUser, Depends(authenticated_user)]
Principal = Annotated[HumanPrincipal, Depends(organization_principal)]
logger = logging.getLogger(__name__)


class GitHubOAuthQueryMiddleware:
    """Keep OAuth codes/state out of Uvicorn access logs and downstream tracing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") in {"/api/v2/auth/github/callback", "/api/v2/auth/github/installation/callback"}:
            scope["github_oauth_query"] = parse_qs(
                scope.get("query_string", b"").decode("ascii", errors="replace")
            )
            scope["query_string"] = b""
            scope["raw_path"] = scope["path"].encode()
        await self.app(scope, receive, send)


def configured():
    callback = urlsplit(settings.github_login_callback_url)
    frontend = urlsplit(settings.github_frontend_url)
    return bool(
        settings.github_login_enabled
        and settings.github_client_id
        and settings.github_client_secret
        and settings.integration_encryption_key
        and callback.scheme == "https"
        and callback.path == "/api/v2/auth/github/callback"
        and not callback.query
        and not callback.fragment
        and callback.hostname
        and frontend.scheme in {"https", "http"}
        and frontend.hostname
        and not frontend.query
        and not frontend.fragment
        and frontend.path in {"", "/"}
        and not frontend.username
        and not frontend.password
        and (frontend.scheme == "https" or frontend.hostname in {"localhost", "127.0.0.1", "::1"})
    )


def enabled():
    if not configured():
        raise HTTPException(409, "GitHub sign-in is not configured yet.")


def decrypt_saved(ciphertext: str | None) -> str:
    if not ciphertext:
        raise HTTPException(409, "Saved GitHub authorization is unavailable. Start again.")
    return decrypt_integration_secret(ciphertext)


class StartRequest(BaseModel):
    purpose: Literal["signin", "signup", "link"]
    browser_challenge: str = Field(pattern=r"^[a-f0-9]{64}$")


class ConnectionContinuation(BaseModel):
    installation_id: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")


def installation_url():
    return (
        f"https://github.com/apps/{quote(settings.github_app_slug, safe='')}/installations/new"
        if settings.github_app_slug else None
    )


class ExchangeRequest(BaseModel):
    flow_id: UUID
    browser_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


@router.get("/auth/github/readiness")
async def readiness():
    return {"enabled": configured()}


@router.post("/auth/github/start")
async def start(
    body: StartRequest,
    request: Request,
    session: Session,
    authorization: Annotated[str | None, Header()] = None,
):
    enabled()
    redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
    try:
        key = "auth:github:start:" + digest(request.client.host if request.client else "unknown")
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 3600)
        if count > 20:
            raise HTTPException(429, "Too many GitHub sign-in attempts. Try again later.")
    finally:
        await redis.aclose()
    owner = None
    if body.purpose == "link":
        owner = (await authenticated_user(authorization)).id
    now = datetime.now(UTC)
    # Expired handoffs cannot retain GitHub credentials indefinitely.
    await session.execute(
        update(GitHubAuthFlow)
        .where(
            GitHubAuthFlow.expires_at <= now,
            GitHubAuthFlow.credential_ciphertext.is_not(None),
        )
        .values(credential_ciphertext=None, status="expired")
    )
    await session.execute(
        update(GitHubOnboarding)
        .where(
            GitHubOnboarding.expires_at <= now,
            GitHubOnboarding.credential_ciphertext.is_not(None),
        )
        .values(credential_ciphertext=None, status="expired")
    )
    nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    row = GitHubAuthFlow(
        state_hash=digest(nonce),
        browser_challenge=body.browser_challenge,
        purpose=body.purpose,
        user_id=owner,
        status="authorizing",
        expires_at=now + timedelta(minutes=15),
        credential_ciphertext=encrypt_integration_secret(json.dumps({"pkce": verifier})),
    )
    session.add(row)
    await session.commit()
    url = await ExpandedGitHubProvider().auth.initiate_authorization(
        state=nonce,
        callback_url=settings.github_login_callback_url,
        code_challenge=pkce_challenge(verifier),
    )
    return {"authorizationUrl": url}


@router.get("/auth/github/callback", include_in_schema=False)
async def callback(request: Request, session: Session):
    enabled()
    query = request.scope.get("github_oauth_query", {})
    state, code = query.get("state", [""])[0], query.get("code", [""])[0]
    row = await session.scalar(
        select(GitHubAuthFlow).where(GitHubAuthFlow.state_hash == digest(state)).with_for_update()
    )
    row = check_flow(row, {"authorizing"})
    flow_id = row.id
    verifier = json.loads(decrypt_saved(row.credential_ciphertext))["pkce"]
    # Consume the single-use exchange before network I/O, without an open DB transaction.
    row.status, row.credential_ciphertext = "exchanging", None
    await session.commit()
    data, failure = None, None
    try:
        if not code or query.get("error"):
            raise HTTPException(400, "GitHub sign-in was cancelled. Start again when ready.")
        provider = ExpandedGitHubProvider()
        bundle = await provider.auth.complete_authorization(
            code=code, callback_url=settings.github_login_callback_url, code_verifier=verifier
        )
        # Existing identities can sign in without requesting email permission again.
        emails = []
        if row.purpose == "signup":
            emails = (await provider.api("GET", "/user/emails", bundle.access_token)).data
        data = {
            "bundle": encode_bundle(bundle),
            "email": verified_email(emails) if row.purpose == "signup" else None,
            "name": bundle.renewal_metadata.get("login"),
        }
    except (ProviderTransportError, HTTPException, RegistrationFailure) as error:
        failure = (
            error.safe_message
            if isinstance(error, ProviderTransportError)
            else str(error.detail)
            if isinstance(error, HTTPException)
            else "Verify a valid primary email on GitHub before signup."
        )
        logger.warning(
            "GitHub authentication callback rejected category=%s",
            error.code if isinstance(error, ProviderTransportError) else "identity_or_consent",
        )
    except Exception as error:  # noqa: BLE001 - provider details may contain secrets; log only the type
        logger.warning("GitHub authentication callback failed error_type=%s", type(error).__name__)
        failure = "GitHub sign-in could not finish. Start again; no account was created."
    row = await session.scalar(
        select(GitHubAuthFlow).where(GitHubAuthFlow.id == flow_id).with_for_update()
    )
    row = check_flow(row, {"exchanging"})
    row.failure_reason = failure
    row.status = "authorized" if data else "failed"
    row.credential_ciphertext = encrypt_integration_secret(json.dumps(data)) if data else None
    await session.commit()
    # Only a non-authoritative flow ID is returned; completion requires the browser secret.
    return RedirectResponse(
        settings.github_frontend_url.rstrip("/")
        + "/?"
        + urlencode({"github_auth": str(flow_id)})
        + ("#/signup" if row.purpose == "signup" else "#/login"),
        status_code=303,
        headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
    )


@router.post("/auth/github/exchange")
async def exchange(
    body: ExchangeRequest, session: Session, authorization: Annotated[str | None, Header()] = None
):
    enabled()
    row = await session.scalar(
        select(GitHubAuthFlow).where(GitHubAuthFlow.id == body.flow_id).with_for_update()
    )
    row = check_flow(row, {"authorized", "failed"}, verifier=body.browser_verifier)
    if row.status == "failed":
        raise HTTPException(
            400, row.failure_reason or "GitHub sign-in could not finish. Start again."
        )
    if row.purpose == "link":
        current = await authenticated_user(authorization)
        if current.id != row.user_id:
            raise HTTPException(403, "Sign in with the account that started GitHub linking.")
    data = json.loads(decrypt_saved(row.credential_ciphertext))
    bundle = decode_bundle(data["bundle"])
    identity = await resolve_identity(
        session,
        subject=bundle.account_id,
        email=data["email"],
        name=data["name"],
        purpose=row.purpose,
        linking_user_id=row.user_id,
    )
    if identity is None or identity.email is None:
        raise HTTPException(409, "The linked account is unavailable. Start sign-in again.")
    row.user_id = identity.id
    connect_requested = row.purpose in {"signup", "link"}
    pending_setup_id = None
    if row.purpose == "signin" and settings.github_expanded_enabled and settings.integration_foundation_enabled:
        pending_setup_id = await session.scalar(
            select(GitHubAuthFlow.id).where(
                GitHubAuthFlow.user_id == identity.id,
                GitHubAuthFlow.id != row.id,
                GitHubAuthFlow.status == "connector_pending",
                GitHubAuthFlow.expires_at > datetime.now(UTC),
            ).order_by(GitHubAuthFlow.created_at.desc()).limit(1)
        )
        if not pending_setup_id:
            existing = await session.scalar(
                select(GitHubInstallationBinding.id).join(
                    IntegrationConnectionState,
                    IntegrationConnectionState.connection_id == GitHubInstallationBinding.connection_id,
                ).where(
                    GitHubInstallationBinding.verified_user_id == bundle.account_id,
                    GitHubInstallationBinding.status == "active",
                    IntegrationConnectionState.owner_id == identity.id,
                    IntegrationConnectionState.ownership_state == "owned",
                    IntegrationConnectionState.authorization_state == "connected",
                ).limit(1)
            )
            # A fresh login can offer optional setup after an expired or skipped handoff.
            # Already connected users go straight to their workspace.
            connect_requested = existing is None
    row.status = "connector_pending" if connect_requested else "completed"
    row.expires_at = datetime.now(UTC) + timedelta(hours=1)
    if connect_requested:
        pending_setup_id = row.id
    else:
        row.credential_ciphertext = None
    await session.commit()
    store = RedisSessionStore.from_settings()
    try:
        token = await store.create(identity.id)
    finally:
        await store.close()
    return {
        **_payload(AuthenticatedUser(identity.id, identity.email, identity.display_name), token),
        "githubSetupId": str(pending_setup_id) if pending_setup_id else None,
    }


@router.get("/auth/github/setup/{flow_id}")
async def setup_status(flow_id: UUID, user: User, session: Session):
    row = await session.get(GitHubAuthFlow, flow_id)
    row = check_flow(row, {"connector_pending", "connected"}, user_id=user.id)
    onboarding = await session.get(GitHubOnboarding, row.onboarding_id) if row.onboarding_id else None
    return {"status": row.status, "organizationId": str(onboarding.organization_id) if onboarding else None}


@router.post("/auth/github/setup/{flow_id}/dismiss")
async def dismiss_setup(flow_id: UUID, user: User, session: Session):
    row = await session.scalar(
        select(GitHubAuthFlow).where(GitHubAuthFlow.id == flow_id).with_for_update()
    )
    row = check_flow(row, {"connector_pending", "connected", "dismissed"}, user_id=user.id)
    if row.onboarding_id:
        onboarding = await session.get(GitHubOnboarding, row.onboarding_id)
        if onboarding and onboarding.status == "select_installation":
            onboarding.status, onboarding.credential_ciphertext = "dismissed", None
    row.status, row.credential_ciphertext = "dismissed", None
    await session.commit()
    return {"status": "dismissed"}


@router.post("/organizations/{organization_id}/github/signup-connection/{flow_id}")
async def continue_connection(
    organization_id: UUID, flow_id: UUID, principal: Principal, session: Session,
    body: ConnectionContinuation | None = None,
):
    try:
        return await attach_signup_connection(
            organization_id, flow_id, principal, session,
            installation_id=body.installation_id if body else None,
        )
    except ProviderTransportError as error:
        await session.rollback()
        logger.warning(
            "GitHub signup connection blocked category=%s retryable=%s", error.code, error.retryable
        )
        raise HTTPException(
            503 if error.retryable else 409,
            error.safe_message
            + " You remain signed in. Retry setup or connect GitHub from Connections.",
        ) from error


async def attach_signup_connection(organization_id, flow_id, principal, session, *, installation_id=None):
    enabled()
    if not settings.github_expanded_enabled or not settings.integration_foundation_enabled:
        raise HTTPException(409, "GitHub connector setup is disabled. You remain signed in.")
    require_permission(principal, "integrations.manage")
    row = await session.scalar(
        select(GitHubAuthFlow).where(GitHubAuthFlow.id == flow_id).with_for_update()
    )
    row = check_flow(row, {"connector_pending", "connected"}, user_id=principal.user_id)
    if row.onboarding_id:
        onboarding = await session.get(GitHubOnboarding, row.onboarding_id)
        if not onboarding or onboarding.organization_id != organization_id:
            raise HTTPException(409, "This GitHub setup belongs to a different workspace.")
        if onboarding.status == "completed":
            row.status = "connected"
            await session.commit()
            return {"status": "connected", "connectionId": str(onboarding.reconnect_connection_id)}
    else:
        data = json.loads(decrypt_saved(row.credential_ciphertext))
        onboarding = GitHubOnboarding(
            id=uuid4(),
            organization_id=organization_id,
            owner_id=principal.user_id,
            state_hash=digest(secrets.token_urlsafe(32)),
            expires_at=row.expires_at,
            status="select_installation",
            credential_ciphertext=encrypt_integration_secret(data["bundle"]),
        )
        session.add(onboarding)
        await session.flush()
        row.onboarding_id, row.credential_ciphertext = onboarding.id, None
        await session.commit()
    onboarding = check_flow(onboarding, {"select_installation"})
    bundle = decode_bundle(decrypt_saved(onboarding.credential_ciphertext))
    # Discovery is read-only. Do not hold the flow lock across GitHub requests;
    # dismissal or permission changes must be checked again before attachment.
    await session.commit()
    provider = ExpandedGitHubProvider()
    candidates = []
    for page in range(1, 11):
        entries = (
            await provider.api(
                "GET", f"/user/installations?per_page=100&page={page}", bundle.access_token
            )
        ).data["installations"]
        candidates.extend(i for i in entries if str(i["app_id"]) == settings.github_app_id)
        if len(entries) < 100:
            break
    else:
        raise HTTPException(
            409, "Installation discovery exceeded its page budget. Connect GitHub from Connections."
        )
    # Sessions retain loaded rows after commit; force a refresh to see a concurrent
    # dismissal or completed attachment instead of accepting cached flow state.
    row = await session.scalar(
        select(GitHubAuthFlow).where(GitHubAuthFlow.id == flow_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    row = check_flow(row, {"connector_pending", "connected"}, user_id=principal.user_id)
    onboarding = await session.get(
        GitHubOnboarding, row.onboarding_id, populate_existing=True, with_for_update=True,
    )
    if not onboarding or onboarding.organization_id != organization_id or onboarding.owner_id != principal.user_id:
        raise HTTPException(409, "This GitHub setup belongs to a different workspace or account.")
    if onboarding.status == "completed":
        await session.commit()
        return {"status": "connected", "connectionId": str(onboarding.reconnect_connection_id)}
    onboarding = check_flow(onboarding, {"select_installation"}, user_id=None)
    personal = [
        i
        for i in candidates
        if not i.get("suspended_at")
        and i["account"].get("type") == "User"
        and str(i["account"]["id"]) == bundle.account_id
    ]
    selected = None
    if installation_id:
        selected = next((i for i in candidates if str(i["id"]) == installation_id and not i.get("suspended_at")), None)
        if not selected:
            raise HTTPException(409, "That GitHub installation is not available to your account yet. Retry setup or skip for now.")
    elif len(personal) == 1 and len(candidates) == 1:
        selected = personal[0]
    if selected:
        result = await connect_installation(
            organization_id, str(selected["id"]), principal, session, onboarding, bundle
        )
        row.status = "connected"
        await session.commit()
        return {"status": "connected", **result}
    return {
        "status": "select_installation" if candidates else "installation_required",
        "onboardingId": str(onboarding.id),
        "installationUrl": installation_url(),
        "installations": [
            {"id": str(i["id"]), "account": i["account"].get("login", "GitHub account"),
             "suspended": bool(i.get("suspended_at"))} for i in candidates
        ],
    }


@router.post("/organizations/{organization_id}/github/setup/{flow_id}/install")
async def start_installation(
    organization_id: UUID, flow_id: UUID, principal: Principal, session: Session,
):
    enabled()
    if not settings.github_expanded_enabled or not settings.integration_foundation_enabled:
        raise HTTPException(409, "GitHub connector setup is disabled. You can skip for now.")
    require_permission(principal, "integrations.manage")
    row = await session.scalar(
        select(GitHubAuthFlow).where(GitHubAuthFlow.id == flow_id).with_for_update()
    )
    row = check_flow(row, {"connector_pending"}, user_id=principal.user_id)
    onboarding = await session.get(GitHubOnboarding, row.onboarding_id) if row.onboarding_id else None
    onboarding = check_flow(onboarding, {"select_installation"})
    if onboarding.organization_id != organization_id or onboarding.owner_id != principal.user_id:
        raise HTTPException(403, "This GitHub setup belongs to a different workspace or account.")
    url = installation_url()
    if not url:
        raise HTTPException(409, "The GitHub App installation link is not configured. You can skip setup for now.")
    # OAuth has already completed. Reuse its hashed-state slot for a new, single-use
    # installation return correlation; this nonce never authorizes a connection.
    nonce = secrets.token_urlsafe(32)
    row.state_hash = digest(nonce)
    await session.commit()
    return {"installationUrl": url + "?" + urlencode({"state": nonce})}


@router.get("/auth/github/installation/callback", include_in_schema=False)
async def installation_callback(request: Request, session: Session):
    enabled()
    query = request.scope.get("github_oauth_query", {})
    state = query.get("state", [""])[0]
    installation_id = query.get("installation_id", [""])[0]
    if not state:
        # An installation started outside Audoryn has no saved task to resume.
        return RedirectResponse(settings.github_frontend_url.rstrip("/") + "/#/app", status_code=303,
                                headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
    row = await session.scalar(
        select(GitHubAuthFlow).where(GitHubAuthFlow.state_hash == digest(state)).with_for_update()
    )
    row = check_flow(row, {"connector_pending"})
    onboarding = await session.get(GitHubOnboarding, row.onboarding_id) if row.onboarding_id else None
    onboarding = check_flow(onboarding, {"select_installation"})
    if onboarding.owner_id != row.user_id:
        raise HTTPException(403, "GitHub setup owner changed. Start setup again.")
    if not re.fullmatch(r"[0-9]{1,20}", installation_id):
        raise HTTPException(400, "GitHub did not return an installation. Retry setup or skip for now.")
    row.state_hash = digest(secrets.token_urlsafe(32))
    await session.commit()
    # This ID is only a hint. Authenticated continuation rechecks current GitHub
    # access, exact App/installation, membership and permissions before attaching.
    return RedirectResponse(
        settings.github_frontend_url.rstrip("/") + "/?" + urlencode({
            "github_installation": str(row.id), "github_installation_id": installation_id,
        }) + "#/app", status_code=303,
        headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
    )
