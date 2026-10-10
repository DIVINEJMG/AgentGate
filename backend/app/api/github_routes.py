"""GitHub App onboarding has no pre-existing token-connection requirement."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import require_permission
from app.application.services.integration_credentials import decode_bundle, encode_bundle
from app.application.services.integration_foundation import IntegrationFoundation
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.native.http import ProviderTransportError
from app.infrastructure.database.models import (
    ExternalAuthIdentity,
    GitHubInstallationBinding,
    GitHubOnboarding,
    OrganizationMembership,
)
from app.infrastructure.database.session import database_session
from app.infrastructure.secrets.integration_crypto import (
    IntegrationCipherError,
    decrypt_integration_secret,
    encrypt_integration_secret,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["github"])
callback_router = APIRouter(tags=["github"])
Principal = Annotated[HumanPrincipal, Depends(organization_principal)]
Session = Annotated[AsyncSession, Depends(database_session)]


def enabled():
    if not settings.github_expanded_enabled or not settings.integration_foundation_enabled:
        raise HTTPException(404, "Expanded GitHub integration is disabled.")


def require_credential_vault():
    if not settings.integration_encryption_key:
        raise HTTPException(
            409,
            "Configure INTEGRATION_ENCRYPTION_KEY and restart FastAPI before connecting GitHub.",
        )


class InstallationChoice(BaseModel):
    onboarding_id: UUID
    installation_id: str = Field(pattern=r"^[0-9]{1,20}$")


@router.get("/organizations/{organization_id}/github/readiness")
async def readiness(organization_id: UUID, principal: Principal, session: Session):
    require_permission(principal, "integrations.read")
    # Older connector deployments need no login tables when login is disabled.
    linked = False
    if settings.github_login_enabled:
        linked = (await session.scalar(
            select(ExternalAuthIdentity.id).where(
                ExternalAuthIdentity.user_id == principal.user_id,
                ExternalAuthIdentity.provider == "github",
            )
        )) is not None
    return {
        "enabled": settings.github_expanded_enabled and settings.integration_foundation_enabled,
        "authenticationConfigured": bool(
            settings.github_app_id
            and settings.github_client_id
            and settings.github_client_secret
            and settings.github_app_private_key
            and settings.github_callback_url
            and settings.integration_encryption_key
        ),
        "loginEnabled": settings.github_login_enabled,
        "signInLinked": linked,
        "credentialVaultConfigured": bool(settings.integration_encryption_key),
        "eventsEnabled": settings.github_events_enabled,
        "codingEnabled": settings.coding_execution_enabled,
        "installationUrl": f"https://github.com/apps/{settings.github_app_slug}/installations/new"
        if settings.github_app_slug
        else None,
    }


@router.get("/organizations/{organization_id}/github/actions")
async def actions(organization_id: UUID, principal: Principal):
    enabled()
    require_permission(principal, "integrations.read")
    provider = ExpandedGitHubProvider()
    return {
        "actions": [
            {
                "scope": c.scope,
                "operation": c.operation,
                "description": c.description,
                "resourceType": c.resource_type,
                "approval": c.approval_recommendation,
                "risk": c.risk,
                "permissions": provider.actions[c.operation].permission,
            }
            for c in provider.manifest.capabilities
        ]
    }


@router.get("/organizations/{organization_id}/work-items/{work_item_id}/coding")
async def coding_status(
    organization_id: UUID, work_item_id: UUID, principal: Principal, session: Session
):
    enabled()
    require_permission(principal, "jobs.read")
    from app.infrastructure.database.models import (
        CodingCommand,
        CodingSession,
        IntegrationResource,
        WorkItem,
    )

    item = await session.get(WorkItem, work_item_id)
    if not item or item.organization_id != organization_id:
        raise HTTPException(404, "Work item not found.")
    rows = (
        await session.scalars(
            select(CodingSession).where(
                CodingSession.organization_id == organization_id,
                CodingSession.work_item_id == work_item_id,
            )
        )
    ).all()
    result = []
    for row in rows:
        resource = await session.get(IntegrationResource, row.resource_id)
        if not resource or not await IntegrationFoundation(session).can_use(
            resource.connection_id, organization_id, principal.user_id
        ):
            continue
        commands = (
            await session.scalars(
                select(CodingCommand)
                .where(CodingCommand.session_id == row.id)
                .order_by(CodingCommand.created_at)
            )
        ).all()
        result.append(
            {
                "id": str(row.id),
                "resource": resource.display_name,
                "status": row.status,
                "baseSha": row.base_sha,
                "activeSeconds": row.active_seconds,
                "expiresAt": row.expires_at.isoformat(),
                "artifactId": row.evidence.get("exportArtifactId"),
                "initialization": {
                    "stage": (row.evidence.get("initialization") or {}).get("stage"),
                    "deadlineAt": (row.evidence.get("initialization") or {}).get("deadline"),
                    "fileCount": (row.evidence.get("initialization") or {}).get("fileCount", 0),
                    "confirmedBundles": len((row.evidence.get("initialization") or {}).get("confirmedBundles", [])),
                    "totalBundles": len((row.evidence.get("initialization") or {}).get("bundles", [])),
                },
                "commands": [
                    {
                        "id": str(c.id),
                        "command": ExpandedGitHubProvider._sanitize(c.command),
                        "status": c.status,
                        "output": ExpandedGitHubProvider._sanitize(c.output),
                    }
                    for c in commands
                ],
            }
        )
    return {"sessions": result, "enabled": settings.coding_execution_enabled}


@router.post("/organizations/{organization_id}/work-items/{work_item_id}/coding/cancel")
async def cancel_coding(
    organization_id: UUID, work_item_id: UUID, principal: Principal, session: Session
):
    enabled()
    require_permission(principal, "jobs.run")
    from app.execution.coding.e2b import E2BCodingRuntime
    from app.execution.coding.service import export, release_unused_budget
    from app.infrastructure.database.models import CodingSession, IntegrationResource, WorkItem
    from app.infrastructure.redis.coordination import RedisCoordinator

    item = await session.get(WorkItem, work_item_id)
    if not item or item.organization_id != organization_id:
        raise HTTPException(404, "Work item not found.")
    rows = (
        await session.scalars(
            select(CodingSession).where(
                CodingSession.organization_id == organization_id,
                CodingSession.work_item_id == work_item_id,
                CodingSession.status.in_({"creating", "creating_uncertain", "running", "paused"}),
            )
        )
    ).all()
    from app.execution.providers.integration_hooks import HookUnavailable

    try:
        runtime = E2BCodingRuntime()
    except HookUnavailable as error:
        raise HTTPException(
            503,
            "Coding runtime configuration is unavailable; retained sessions require cleanup after reconnection.",
        ) from error
    coordinator = RedisCoordinator.from_settings()
    try:
        for row in rows:
            resource = await session.get(IntegrationResource, row.resource_id)
            if not resource or not await IntegrationFoundation(session).can_use(
                resource.connection_id, organization_id, principal.user_id
            ):
                raise HTTPException(403, "Coding workspace access is not shared with you.")
            lease = await coordinator.acquire_lock(
                f"coding-workspace:{work_item_id}:{row.resource_id}", ttl_seconds=120
            )
            if not lease:
                raise HTTPException(
                    409, "Workspace operation is in progress. Retry cancellation shortly."
                )
            try:
                if row.status in {"creating", "creating_uncertain"} and not row.sandbox_id:
                    row.sandbox_id = await runtime.find(str(row.id))
                if row.sandbox_id:
                    from types import SimpleNamespace

                    request = SimpleNamespace(
                        organization_id=organization_id,
                        work_item_id=work_item_id,
                        run_id=None,
                        idempotency_key="coding-cancel:" + str(row.id),
                        resource=SimpleNamespace(id=str(row.resource_id)),
                    )
                    try:
                        await runtime.connect(row.sandbox_id, 120)
                        if row.evidence.get("baselineArtifactId"):
                            await export(runtime, row, request, {"constraints": {}})
                    except Exception:  # noqa: BLE001 - cancellation must stop compute even if its diff cannot be exported
                        row.evidence = {
                            **row.evidence,
                            "cleanupWarning": "Cancellation could not export the latest changes. Previously saved evidence remains available.",
                        }
                    await runtime.delete(row.sandbox_id)
                if row.active_since:
                    row.active_seconds += min(
                        settings.coding_idle_seconds,
                        max(0, int((datetime.now(UTC) - row.active_since).total_seconds())),
                    )
                row.status, row.active_since = "cancelled", None
                await release_unused_budget(session, row)
                await session.commit()
            finally:
                await coordinator.release_lock(lease)
    finally:
        await coordinator.close()
    return {
        "status": "cancelled",
        "message": "Retained evidence remains private. Cancellation did not publish to GitHub; inspect any cleanup warnings before relying on the saved diff.",
    }


@router.get("/organizations/{organization_id}/work-items/{work_item_id}/coding/{session_id}/diff")
async def coding_diff(
    organization_id: UUID,
    work_item_id: UUID,
    session_id: UUID,
    principal: Principal,
    session: Session,
):
    enabled()
    require_permission(principal, "jobs.read")
    import json
    from types import SimpleNamespace

    from app.execution.coding.service import load_artifact
    from app.infrastructure.database.models import CodingSession, IntegrationResource

    row = await session.get(CodingSession, session_id)
    if not row or row.organization_id != organization_id or row.work_item_id != work_item_id:
        raise HTTPException(404, "Coding evidence not found.")
    resource = await session.get(IntegrationResource, row.resource_id)
    if not resource or not await IntegrationFoundation(session).can_use(
        resource.connection_id, organization_id, principal.user_id
    ):
        raise HTTPException(404, "Coding evidence not found.")
    identity = row.evidence.get("exportArtifactId")
    if not identity:
        return {"diff": "", "baseSha": row.base_sha, "available": False}
    content = await load_artifact(
        identity, SimpleNamespace(organization_id=organization_id, work_item_id=work_item_id)
    )
    data = json.loads(content)
    return {
        "diff": ExpandedGitHubProvider._sanitize(data["diff"][:100000]),
        "baseSha": row.base_sha,
        "sourceFingerprint": data["sourceFingerprint"],
        "available": True,
        "truncated": len(data["diff"]) > 100000,
    }


@router.post("/organizations/{organization_id}/github/authorize")
async def authorize(
    organization_id: UUID, principal: Principal, session: Session, connection_id: UUID | None = None
):
    enabled()
    require_permission(principal, "integrations.manage")
    require_credential_vault()
    if connection_id:
        from app.api.integration_foundation_routes import owned

        await owned(session, organization_id, connection_id, principal)
        binding = await session.scalar(
            select(GitHubInstallationBinding).where(
                GitHubInstallationBinding.connection_id == connection_id
            )
        )
        if not binding:
            raise HTTPException(
                409, "Static-token connections must be kept separate from GitHub App onboarding."
            )
    if not settings.github_callback_url or urlsplit(settings.github_callback_url).scheme != "https":
        raise HTTPException(409, "Configure the GitHub App HTTPS callback URL first.")
    nonce = secrets.token_urlsafe(32)
    url = await ExpandedGitHubProvider().initiate_authorization(
        state=nonce, callback_url=settings.github_callback_url
    )
    row = GitHubOnboarding(
        organization_id=organization_id,
        owner_id=principal.user_id,
        reconnect_connection_id=connection_id,
        state_hash=hashlib.sha256(nonce.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        status="authorizing",
    )
    session.add(row)
    await session.commit()
    return {"authorizationUrl": url}


@callback_router.get("/integrations/github/callback")
async def callback(code: str, state: str, session: Session):
    enabled()
    require_credential_vault()
    row = await session.scalar(
        select(GitHubOnboarding)
        .where(GitHubOnboarding.state_hash == hashlib.sha256(state.encode()).hexdigest())
        .with_for_update()
    )
    if not row or row.status != "authorizing" or row.expires_at <= datetime.now(UTC):
        raise HTTPException(400, "GitHub authorization state is expired or already consumed.")
    member = await session.scalar(
        select(OrganizationMembership.id).where(
            OrganizationMembership.organization_id == row.organization_id,
            OrganizationMembership.user_id == row.owner_id,
        )
    )
    if not member:
        raise HTTPException(403, "Organization membership changed during authorization.")
    try:
        bundle = await ExpandedGitHubProvider().complete_authorization(
            code=code, callback_url=settings.github_callback_url
        )
        row.credential_ciphertext = encrypt_integration_secret(encode_bundle(bundle))
    except ProviderTransportError as error:
        # Codes are single-use. A partially completed exchange must start again,
        # and provider errors must never expose the callback URL or credentials.
        row.status = "failed"
        await session.commit()
        raise HTTPException(
            503 if error.retryable else 400,
            error.safe_message + " Start Connect GitHub App again from Audoryn.",
        ) from error
    except IntegrationCipherError as error:
        row.status = "failed"
        await session.commit()
        raise HTTPException(409, str(error)) from error
    row.status = "select_installation"
    await session.commit()
    # The redirect contains a non-secret session identity. The authenticated owner
    # must finish it; neither this identity nor a webhook confers account access.
    return RedirectResponse(
        settings.github_frontend_url.rstrip("/") + "/?" + urlencode({"github_setup": str(row.id)}),
        status_code=303,
    )


async def pending(session, identity, organization_id, user_id):
    row = await session.scalar(
        select(GitHubOnboarding)
        .where(
            GitHubOnboarding.id == identity,
            GitHubOnboarding.organization_id == organization_id,
            GitHubOnboarding.owner_id == user_id,
        )
        .with_for_update()
    )
    if (
        not row
        or row.status != "select_installation"
        or row.expires_at <= datetime.now(UTC)
        or not row.credential_ciphertext
    ):
        raise HTTPException(400, "GitHub onboarding is unavailable or expired.")
    return row, decode_bundle(decrypt_integration_secret(row.credential_ciphertext))


@router.get("/organizations/{organization_id}/github/onboarding/{onboarding_id}")
async def installations(
    organization_id: UUID, onboarding_id: UUID, principal: Principal, session: Session
):
    enabled()
    require_permission(principal, "integrations.manage")
    _, bundle = await pending(session, onboarding_id, organization_id, principal.user_id)
    provider = ExpandedGitHubProvider()
    results = []
    for page in range(1, 11):
        data = (
            await provider.api(
                "GET", f"/user/installations?per_page=100&page={page}", bundle.access_token
            )
        ).data
        results.extend(
            {
                "id": str(i["id"]),
                "account": i["account"]["login"],
                "suspended": bool(i.get("suspended_at")),
            }
            for i in data["installations"]
            if str(i["app_id"]) == settings.github_app_id
        )
        if len(data["installations"]) < 100:
            break
    else:
        raise HTTPException(409, "Too many installations to list safely. Narrow installation access before retrying.")
    logger.info(
        "GitHub installation selection onboarding=%s matching=%s usable=%s install_url_configured=%s",
        onboarding_id, len(results), sum(not item["suspended"] for item in results),
        bool(settings.github_app_slug),
    )
    return {"installations": results}


@router.post("/organizations/{organization_id}/github/connect")
async def connect(
    organization_id: UUID, body: InstallationChoice, principal: Principal, session: Session
):
    enabled()
    require_permission(principal, "integrations.manage")
    row, user = await pending(session, body.onboarding_id, organization_id, principal.user_id)
    from app.application.services.github_connector import connect_installation

    return await connect_installation(
        organization_id, body.installation_id, principal, session, row, user
    )
