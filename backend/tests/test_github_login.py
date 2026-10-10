import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.api import github_auth_routes as routes
from app.application.services.integration_credentials import encode_bundle
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.execution.providers.integration_hooks import CredentialBundle
from app.infrastructure.auth.github import (
    check_flow,
    digest,
    pkce_challenge,
    resolve_identity,
    verified_email,
)
from app.infrastructure.auth.service import AuthenticatedUser
from app.infrastructure.database.models import (
    ExternalAuthIdentity,
    GitHubAuthFlow,
    GitHubOnboarding,
    HumanIdentity,
)
from app.infrastructure.secrets.integration_crypto import (
    decrypt_integration_secret,
    encrypt_integration_secret,
)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "github_login_enabled", True)
    monkeypatch.setattr(settings, "github_client_id", "test-client")
    monkeypatch.setattr(settings, "github_client_secret", SecretStr("secret"))
    monkeypatch.setattr(settings, "integration_encryption_key", SecretStr("test-vault"))
    monkeypatch.setattr(
        settings, "github_login_callback_url", "https://backend.invalid/api/v2/auth/github/callback"
    )
    monkeypatch.setattr(settings, "github_frontend_url", "http://localhost:5173")
    monkeypatch.setattr(settings, "github_app_id", "77")
    monkeypatch.setattr(settings, "github_expanded_enabled", True)
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)


def flow(**changes):
    return SimpleNamespace(
        id=uuid4(),
        purpose="signup",
        status="authorized",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        browser_challenge=digest("v" * 64),
        user_id=None,
        credential_ciphertext=None,
        onboarding_id=None,
        **changes,
    )


def db(*rows):
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=list(rows) if len(rows) > 1 else None,
                         return_value=rows[0] if rows else None),
        get=AsyncMock(),
        commit=AsyncMock(),
        flush=AsyncMock(),
        rollback=AsyncMock(),
        add=Mock(),
        execute=AsyncMock(),
    )
    def added(row):
        if isinstance(row, GitHubOnboarding):
            session.get.return_value = row
    session.add.side_effect = added
    return session


def test_pkce_rfc7636_vector():
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


@pytest.mark.parametrize("kind", ["expired", "replayed", "wrong_browser", "wrong_owner"])
def test_flow_rejects_expiry_replay_browser_and_cross_user(kind):
    row = flow()
    args = {}
    if kind == "expired":
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if kind == "replayed":
        row.status = "completed"
    if kind == "wrong_browser":
        args["verifier"] = "another-browser"
    if kind == "wrong_owner":
        args["user_id"] = uuid4()
    with pytest.raises(HTTPException):
        check_flow(cast(GitHubAuthFlow, row), {"authorized"}, **args)


def test_only_verified_primary_email_is_accepted():
    assert (
        verified_email([{"email": "User@example.com", "verified": True, "primary": True}])
        == "user@example.com"
    )
    with pytest.raises(HTTPException):
        verified_email([{"email": "user@example.com", "verified": False, "primary": True}])


async def test_signup_creates_identity_without_password_or_workspace():
    session = db(None, None)
    identity = await resolve_identity(
        session, subject="123", email="user@example.com", name="GitHub User", purpose="signup"
    )
    assert identity.subject == "github:123"
    rows = [call.args[0] for call in session.add.call_args_list]
    assert [type(row) for row in rows] == [HumanIdentity, ExternalAuthIdentity]
    assert rows[1].user_id == identity.id


async def test_email_collision_does_not_merge_local_account():
    session = db(None, uuid4())
    with pytest.raises(HTTPException) as error:
        await resolve_identity(
            session, subject="123", email="user@example.com", name=None, purpose="signup"
        )
    assert error.value.status_code == 409
    session.add.assert_not_called()


async def test_login_uses_stable_identity_and_does_not_update_email():
    user_id = uuid4()
    session = db(SimpleNamespace(user_id=user_id))
    session.get.return_value = HumanIdentity(
        id=user_id, subject="local:old", email="old@example.com"
    )
    identity = await resolve_identity(
        session, subject="123", email="changed@example.com", name="renamed", purpose="signin"
    )
    assert identity.email == "old@example.com"
    session.add.assert_not_called()


async def test_unlinked_login_does_not_create_account():
    session = db(None)
    with pytest.raises(HTTPException):
        await resolve_identity(session, subject="123", email=None, name=None, purpose="signin")
    session.add.assert_not_called()


async def test_link_cannot_take_another_users_identity():
    session = db(SimpleNamespace(user_id=uuid4()))
    with pytest.raises(HTTPException):
        await resolve_identity(
            session, subject="123", email=None, name=None, purpose="link", linking_user_id=uuid4()
        )
    session.add.assert_not_called()


async def test_callback_commits_before_external_exchange_and_keeps_tokens_out_of_redirect(
    configured, monkeypatch
):
    row = flow()
    row.status = "authorizing"
    row.credential_ciphertext = encrypt_integration_secret(json.dumps({"pkce": "pkce-secret"}))
    session = db(row, row)
    bundle = CredentialBundle(
        access_token="github-private-token", account_id="123", renewal_metadata={"login": "User"}
    )

    async def complete(**kwargs):
        session.commit.assert_awaited_once()
        assert kwargs["code_verifier"] == "pkce-secret"
        return bundle

    provider = SimpleNamespace(
        auth=SimpleNamespace(complete_authorization=AsyncMock(side_effect=complete)),
        api=AsyncMock(
            return_value=SimpleNamespace(
                data=[{"email": "user@example.com", "primary": True, "verified": True}]
            )
        ),
    )
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: provider)
    request = Request(
        {"type": "http", "github_oauth_query": {"state": ["state"], "code": ["code"]}}
    )
    response = await routes.callback(request, cast(AsyncSession, session))
    assert row.status == "authorized"
    assert "github-private-token" not in response.headers["location"]
    assert "github-private-token" not in row.credential_ciphertext
    assert "github-private-token" in decrypt_integration_secret(row.credential_ciphertext)


async def test_callback_cancellation_consumes_state_without_provider_call(configured, monkeypatch):
    row = flow()
    row.status = "authorizing"
    row.credential_ciphertext = encrypt_integration_secret(json.dumps({"pkce": "secret"}))
    session = db(row, row)
    provider = Mock()
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", provider)
    response = await routes.callback(
        Request({"type": "http", "github_oauth_query": {"state": ["state"], "error": ["denied"]}}),
        cast(AsyncSession, session),
    )
    assert response.status_code == 303 and row.status == "failed"
    assert row.credential_ciphertext is None
    assert "cancelled" in row.failure_reason
    provider.assert_not_called()


async def test_oauth_middleware_removes_secrets_from_access_log_scope():
    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope)

    scope = {
        "type": "http",
        "path": "/api/v2/auth/github/callback",
        "query_string": b"code=private-code&state=private-state",
    }
    await routes.GitHubOAuthQueryMiddleware(downstream)(scope, None, None)
    assert scope["query_string"] == b""
    assert seen[0]["github_oauth_query"]["code"] == ["private-code"]


def connection_flow():
    bundle = CredentialBundle(access_token="private-token", account_id="123")
    row = flow()
    row.status = "connector_pending"
    row.user_id = uuid4()
    row.credential_ciphertext = encrypt_integration_secret(
        json.dumps({"bundle": encode_bundle(bundle)})
    )
    return row


async def test_signup_auto_connects_personal_installation_once(configured, monkeypatch):
    row = connection_flow()
    session = db(row)
    principal = SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})
    provider = SimpleNamespace(
        api=AsyncMock(
            return_value=SimpleNamespace(
                data={
                    "installations": [
                        {"id": 8, "app_id": 77, "account": {"id": 123, "type": "User"}}
                    ]
                }
            )
        )
    )
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: provider)
    attach = AsyncMock(return_value={"connectionId": "connection", "state": "connected"})
    monkeypatch.setattr(routes, "connect_installation", attach)
    result = await routes.continue_connection(uuid4(), row.id, cast(HumanPrincipal, principal), cast(AsyncSession, session))
    assert result["status"] == "connected"
    assert row.credential_ciphertext is None
    assert row.onboarding_id is not None
    attach.assert_awaited_once()


@pytest.mark.parametrize(
    "entries, expected",
    [
        ([], "installation_required"),
        (
            [{"id": 9, "app_id": 77, "account": {"id": 999, "type": "Organization"}}],
            "select_installation",
        ),
        (
            [
                {
                    "id": 8,
                    "app_id": 77,
                    "suspended_at": "now",
                    "account": {"id": 123, "type": "User"},
                }
            ],
            "select_installation",
        ),
    ],
)
async def test_missing_organization_or_suspended_installation_requires_user_choice(
    configured, monkeypatch, entries, expected
):
    row = connection_flow()
    session = db(row)
    provider = SimpleNamespace(
        api=AsyncMock(return_value=SimpleNamespace(data={"installations": entries}))
    )
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: provider)
    attach = AsyncMock()
    monkeypatch.setattr(routes, "connect_installation", attach)
    result = await routes.continue_connection(
        uuid4(),
        row.id,
        cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})),
        cast(AsyncSession, session),
    )
    assert result["status"] == expected and result["onboardingId"]
    attach.assert_not_awaited()


async def test_signup_connection_requires_current_org_permission(configured):
    session = db()
    with pytest.raises(HTTPException) as error:
        await routes.continue_connection(
            uuid4(), uuid4(), cast(HumanPrincipal, SimpleNamespace(permissions=set())), cast(AsyncSession, session)
        )
    assert error.value.status_code == 403
    session.scalar.assert_not_awaited()


async def test_signup_connection_cannot_move_to_another_org(configured):
    row = connection_flow()
    row.onboarding_id = uuid4()
    session = db(row)
    session.get.return_value = SimpleNamespace(organization_id=uuid4())
    with pytest.raises(HTTPException) as error:
        await routes.continue_connection(
            uuid4(),
            row.id,
            cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})),
            cast(AsyncSession, session),
        )
    assert error.value.status_code == 409


async def test_completed_connector_handoff_is_idempotent(configured, monkeypatch):
    row = connection_flow()
    row.onboarding_id = uuid4()
    org = uuid4()
    session = db(row)
    session.get.return_value = SimpleNamespace(
        organization_id=org, status="completed", reconnect_connection_id=uuid4()
    )
    attach = AsyncMock()
    monkeypatch.setattr(routes, "connect_installation", attach)
    result = await routes.continue_connection(
        org,
        row.id,
        cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})),
        cast(AsyncSession, session),
    )
    assert result["status"] == "connected"
    attach.assert_not_awaited()


def test_disabled_login_needs_no_auth_tables(monkeypatch):
    monkeypatch.setattr(settings, "github_login_enabled", False)
    assert routes.configured() is False


async def test_exchange_returns_existing_session_contract_and_saved_handoff(
    configured, monkeypatch
):
    row = connection_flow()
    row.status = "authorized"
    # The callback stores the verified profile with its encrypted token bundle.
    data = json.loads(decrypt_integration_secret(row.credential_ciphertext))
    data.update(email="user@example.com", name="User")
    row.credential_ciphertext = encrypt_integration_secret(json.dumps(data))
    session = db(row)
    identity = HumanIdentity(
        id=uuid4(), subject="github:123", email="user@example.com", display_name="User"
    )
    resolve = AsyncMock(return_value=identity)
    monkeypatch.setattr(routes, "resolve_identity", resolve)
    store = SimpleNamespace(create=AsyncMock(return_value="audoryn-token"), close=AsyncMock())
    monkeypatch.setattr(routes.RedisSessionStore, "from_settings", lambda: store)
    result = await routes.exchange(
        routes.ExchangeRequest(flow_id=row.id, browser_verifier="v" * 64), cast(AsyncSession, session)
    )
    assert result["accessToken"] == "audoryn-token"
    assert result["user"]["userId"] == str(identity.id)
    assert result["githubSetupId"] == str(row.id)
    assert "private-token" not in str(result)
    assert row.status == "connector_pending"
    store.close.assert_awaited_once()


async def test_link_requires_same_current_audoryn_user(configured, monkeypatch):
    row = connection_flow()
    row.status = "authorized"
    row.purpose = "link"
    session = db(row)
    monkeypatch.setattr(
        routes, "authenticated_user", AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    with pytest.raises(HTTPException) as error:
        await routes.exchange(
            routes.ExchangeRequest(flow_id=row.id, browser_verifier="v" * 64),
            cast(AsyncSession, session),
            "Bearer other",
        )
    assert error.value.status_code == 403
    assert row.status == "authorized"
    session.commit.assert_not_awaited()


async def test_later_login_restores_unfinished_signup_without_reconnecting(configured, monkeypatch):
    row = connection_flow()
    row.status = "authorized"
    row.purpose = "signin"
    data = json.loads(decrypt_integration_secret(row.credential_ciphertext))
    data.update(email=None, name="User")
    row.credential_ciphertext = encrypt_integration_secret(json.dumps(data))
    previous_flow = uuid4()
    session = db(row, previous_flow)
    identity = HumanIdentity(id=row.user_id, subject="github:123", email="user@example.com")
    monkeypatch.setattr(routes, "resolve_identity", AsyncMock(return_value=identity))
    store = SimpleNamespace(create=AsyncMock(return_value="session"), close=AsyncMock())
    monkeypatch.setattr(routes.RedisSessionStore, "from_settings", lambda: store)
    result = await routes.exchange(
        routes.ExchangeRequest(flow_id=row.id, browser_verifier="v" * 64), cast(AsyncSession, session)
    )
    assert result["githubSetupId"] == str(previous_flow)
    assert row.credential_ciphertext is None and row.status == "completed"


async def test_dismissal_clears_pending_credentials_but_keeps_existing_connections(configured):
    row = connection_flow()
    row.onboarding_id = uuid4()
    session = db(row)
    onboarding = SimpleNamespace(status="select_installation", credential_ciphertext="encrypted")
    session.get.return_value = onboarding
    assert (await routes.dismiss_setup(row.id, cast(AuthenticatedUser, SimpleNamespace(id=row.user_id)), cast(AsyncSession, session)))[
        "status"
    ] == "dismissed"
    assert row.credential_ciphertext is None and onboarding.credential_ciphertext is None
    assert onboarding.status == "dismissed"


async def test_start_uses_separate_pkce_and_throttles(configured, monkeypatch):
    request = Request({"type": "http", "client": ("127.0.0.1", 1)})
    redis = SimpleNamespace(incr=AsyncMock(return_value=1), expire=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(routes.Redis, "from_url", lambda *a, **kw: redis)
    provider = SimpleNamespace(
        auth=SimpleNamespace(
            initiate_authorization=AsyncMock(
                return_value="https://github.com/login/oauth/authorize"
            )
        )
    )
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: provider)
    session = db()
    result = await routes.start(
        routes.StartRequest(purpose="signup", browser_challenge=digest("browser")), request, cast(AsyncSession, session)
    )
    saved = session.add.call_args.args[0]
    assert saved.purpose == "signup" and saved.user_id is None
    assert saved.browser_challenge == digest("browser")
    pkce = json.loads(decrypt_integration_secret(saved.credential_ciphertext))["pkce"]
    assert provider.auth.initiate_authorization.call_args.kwargs[
        "code_challenge"
    ] == pkce_challenge(pkce)
    assert "pkce" not in result
    redis.incr.return_value = 21
    with pytest.raises(HTTPException) as error:
        await routes.start(
            routes.StartRequest(purpose="signup", browser_challenge=digest("browser")),
            request,
            cast(AsyncSession, session),
        )
    assert error.value.status_code == 429


async def test_real_http_callback_is_redacted_and_exchange_is_one_use(configured, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from app.infrastructure.database.session import database_session

    row = connection_flow()
    row.status = "authorizing"
    row.credential_ciphertext = encrypt_integration_secret(json.dumps({"pkce": "pkce-secret"}))
    session = db(row, row, row, row)
    bundle = CredentialBundle(access_token="private-token", account_id="123")
    provider = SimpleNamespace(
        auth=SimpleNamespace(complete_authorization=AsyncMock(return_value=bundle)),
        api=AsyncMock(
            return_value=SimpleNamespace(
                data=[{"email": "user@example.com", "primary": True, "verified": True}]
            )
        ),
    )
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: provider)
    identity = HumanIdentity(id=uuid4(), subject="github:123", email="user@example.com")
    monkeypatch.setattr(routes, "resolve_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(
        routes.RedisSessionStore,
        "from_settings",
        lambda: SimpleNamespace(create=AsyncMock(return_value="session"), close=AsyncMock()),
    )
    app = FastAPI()
    app.add_middleware(routes.GitHubOAuthQueryMiddleware)
    app.include_router(routes.router, prefix="/api/v2")

    async def dependency():
        yield session

    app.dependency_overrides[database_session] = dependency
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://backend.invalid"
    ) as client:
        response = await client.get("/api/v2/auth/github/callback?state=state&code=private-code")
        assert response.status_code == 303 and "private-code" not in response.headers["location"]
        response = await client.post(
            "/api/v2/auth/github/exchange",
            json={"flow_id": str(row.id), "browser_verifier": "v" * 64},
        )
        assert response.status_code == 200 and response.json()["accessToken"] == "session"
        response = await client.post(
            "/api/v2/auth/github/exchange",
            json={"flow_id": str(row.id), "browser_verifier": "v" * 64},
        )
        assert response.status_code == 400


async def test_connector_reuses_exact_owned_installation_instead_of_creating_duplicate(
    configured, monkeypatch
):
    from app.api import integration_foundation_routes
    from app.application.services import github_connector as connector

    owner, org, connection_id = uuid4(), uuid4(), uuid4()
    binding = SimpleNamespace(
        connection_id=connection_id,
        installation_id="8",
        verified_user_id="123",
        permissions={},
        status="active",
    )
    credential = SimpleNamespace(ciphertext="old")
    session = db(owner, "owner", binding, binding, credential)
    session.get.return_value = SimpleNamespace(id=connection_id, config={}, status="connected")
    state = SimpleNamespace(authorization_state="connected", reason="", credential_metadata={})
    monkeypatch.setattr(integration_foundation_routes, "owned", AsyncMock(return_value=state))
    user = CredentialBundle(access_token="user-token", account_id="123")
    installation = {
        "account": {"id": 123, "login": "User", "type": "User"},
        "permissions": {"metadata": "read"},
    }

    async def api(method, path, token):
        return SimpleNamespace(
            data={"repositories": [{"id": 900}]}
            if path.startswith("/user/installations/")
            else {"slug": "audoryn"}
        )

    provider = SimpleNamespace(
        auth=SimpleNamespace(
            verify_installation=AsyncMock(return_value=installation),
            installation_bundle=AsyncMock(
                return_value=CredentialBundle(
                    access_token="installation-token", account_id="installation:8"
                )
            ),
        ),
        api=api,
    )
    foundation = SimpleNamespace(discover=AsyncMock())
    monkeypatch.setattr(connector, "ExpandedGitHubProvider", lambda: provider)
    monkeypatch.setattr(connector, "IntegrationFoundation", lambda session: foundation)
    monkeypatch.setattr(connector, "app_jwt", lambda: "signing-token")
    row = SimpleNamespace(
        id=uuid4(),
        owner_id=owner,
        reconnect_connection_id=None,
        status="select_installation",
        credential_ciphertext="encrypted",
    )
    result = await connector.connect_installation(
        org, "8", SimpleNamespace(user_id=owner), session, row, user
    )
    assert result["connectionId"] == str(connection_id)
    assert row.reconnect_connection_id == connection_id and row.status == "completed"
    assert row.credential_ciphertext is None
    session.add.assert_not_called()
    provider.auth.installation_bundle.assert_awaited_once_with("8", repositories=["900"])
    foundation.discover.assert_awaited_once()


async def test_connector_rechecks_membership_after_oauth_before_token_mint(configured, monkeypatch):
    from app.application.services import github_connector as connector

    provider = Mock()
    monkeypatch.setattr(connector, "ExpandedGitHubProvider", provider)
    session = db(uuid4(), None)
    with pytest.raises(HTTPException) as error:
        await connector.connect_installation(
            uuid4(),
            "8",
            SimpleNamespace(user_id=uuid4()),
            session,
            SimpleNamespace(reconnect_connection_id=None),
            CredentialBundle(access_token="private-token", account_id="123"),
        )
    assert error.value.status_code == 403
    provider.assert_not_called()


async def test_multiple_installations_are_not_automatically_chosen(configured, monkeypatch):
    row = connection_flow()
    session = db(row)
    provider = SimpleNamespace(
        api=AsyncMock(
            return_value=SimpleNamespace(
                data={
                    "installations": [
                        {"id": 8, "app_id": 77, "account": {"id": 123, "type": "User"}},
                        {"id": 9, "app_id": 77, "account": {"id": 999, "type": "Organization"}},
                    ]
                }
            )
        )
    )
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: provider)
    attach = AsyncMock()
    monkeypatch.setattr(routes, "connect_installation", attach)
    result = await routes.continue_connection(
        uuid4(),
        row.id,
        cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})),
        cast(AsyncSession, session),
    )
    assert result["status"] == "select_installation"
    attach.assert_not_awaited()


async def test_provider_connection_failure_is_actionable_without_invalidating_login(
    configured, monkeypatch
):
    from app.execution.providers.native.http import ProviderTransportError

    row = connection_flow()
    session = db(row)
    provider = SimpleNamespace(
        api=AsyncMock(
            side_effect=ProviderTransportError(
                code="provider_unavailable",
                retryable=True,
                safe_message="GitHub is temporarily unavailable.",
            )
        )
    )
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: provider)
    with pytest.raises(HTTPException) as error:
        await routes.continue_connection(
            uuid4(),
            row.id,
            cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})),
            cast(AsyncSession, session),
        )
    assert error.value.status_code == 503 and "remain signed in" in error.value.detail
    assert row.status == "connector_pending" and row.onboarding_id is not None
    session.rollback.assert_awaited_once()


async def test_signup_persists_parent_before_external_identity_with_real_foreign_keys():
    from sqlalchemy import create_engine, event, select
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    cast(Table, HumanIdentity.__table__).create(engine)
    cast(Table, ExternalAuthIdentity.__table__).create(engine)
    inserts = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_order(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT"):
            inserts.append(statement.split()[2])

    with Session(engine) as sync:

        class AsyncBridge:
            def add(self, row):
                sync.add(row)

            async def scalar(self, statement):
                return sync.scalar(statement)

            async def get(self, model, identity):
                return sync.get(model, identity)

            async def flush(self):
                sync.flush()

            async def rollback(self):
                sync.rollback()

        session = AsyncBridge()
        identity = await resolve_identity(
            session, subject="123", email="new@example.invalid", name="New", purpose="signup"
        )
        sync.commit()
        linked = sync.scalar(
            select(ExternalAuthIdentity).where(ExternalAuthIdentity.provider_subject == "123")
        )
        assert linked is not None
        assert linked.user_id == identity.id
        assert inserts == ["human_identities", "external_auth_identities"]
        again = await resolve_identity(
            session, subject="123", email=None, name=None, purpose="signin"
        )
        assert again.id == identity.id
        assert len(inserts) == 2
    engine.dispose()


@pytest.mark.parametrize("sqlstate,status", [("23503", 503), ("23505", 409)])
async def test_database_failure_reports_constraint_without_sql_or_secrets(caplog, sqlstate, status):
    from sqlalchemy.exc import IntegrityError

    class DriverError(Exception):
        sqlstate: str
        constraint_name = "external_auth_identities_user_id_fkey"

    driver = DriverError("private-user-data")
    driver.sqlstate = sqlstate
    session = db(None, None)
    session.flush.side_effect = IntegrityError(
        "INSERT private-SQL", {"token": "private-token", "email": "private-email"}, driver
    )
    with pytest.raises(HTTPException) as error:
        await resolve_identity(
            session, subject="123", email="fixture@example.invalid", name=None, purpose="signup"
        )
    assert error.value.status_code == status
    assert f"sqlstate={sqlstate}" in caplog.text
    assert "constraint=external_auth_identities_user_id_fkey" in caplog.text
    assert "private" not in caplog.text
    assert "private" not in error.value.detail
    session.rollback.assert_awaited_once()


@pytest.mark.parametrize("existing", [None, "owned-connection"])
async def test_signin_offers_fresh_optional_setup_only_when_not_connected(configured, monkeypatch, existing):
    row = connection_flow()
    row.status, row.purpose = "authorized", "signin"
    data = json.loads(decrypt_integration_secret(row.credential_ciphertext))
    data.update(email=None, name="User")
    row.credential_ciphertext = encrypt_integration_secret(json.dumps(data))
    session = db(row, None, existing)
    identity = HumanIdentity(id=row.user_id, subject="github:123", email="user@example.com")
    monkeypatch.setattr(routes, "resolve_identity", AsyncMock(return_value=identity))
    store = SimpleNamespace(create=AsyncMock(return_value="session"), close=AsyncMock())
    monkeypatch.setattr(routes.RedisSessionStore, "from_settings", lambda: store)
    result = await routes.exchange(routes.ExchangeRequest(flow_id=row.id, browser_verifier="v" * 64), cast(AsyncSession, session))
    assert result["githubSetupId"] == (str(row.id) if existing is None else None)
    assert row.status == ("connector_pending" if existing is None else "completed")
    assert bool(row.credential_ciphertext) is (existing is None)


def existing_onboarding(row, org):
    row.onboarding_id = uuid4()
    return SimpleNamespace(id=row.onboarding_id, organization_id=org, owner_id=row.user_id,
        status="select_installation", expires_at=row.expires_at,
        credential_ciphertext=encrypt_integration_secret(encode_bundle(CredentialBundle(access_token="private-token", account_id="123"))))


async def test_installation_start_is_workspace_bound_and_state_hashed(configured, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setattr(settings, "github_app_slug", "test-app")
    row, org = connection_flow(), uuid4()
    onboarding = existing_onboarding(row, org)
    session = db(row)
    session.get.return_value = onboarding
    result = await routes.start_installation(org, row.id, cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})), cast(AsyncSession, session))
    url = urlsplit(result["installationUrl"])
    nonce = parse_qs(url.query)["state"][0]
    assert url.hostname == "github.com" and url.path == "/apps/test-app/installations/new"
    assert row.state_hash == digest(nonce) and nonce not in row.state_hash
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("reason", ["wrong_user", "wrong_org", "expired", "dismissed", "no_permission", "missing_slug"])
async def test_installation_start_rejects_invalid_setup(configured, monkeypatch, reason):
    monkeypatch.setattr(settings, "github_app_slug", "test-app" if reason != "missing_slug" else "")
    row, org = connection_flow(), uuid4()
    onboarding = existing_onboarding(row, org)
    session = db(row)
    session.get.return_value = onboarding
    principal = SimpleNamespace(user_id=uuid4() if reason == "wrong_user" else row.user_id, permissions=set() if reason == "no_permission" else {"integrations.manage"})
    if reason == "wrong_org": onboarding.organization_id = uuid4()
    if reason == "expired": row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if reason == "dismissed": row.status = "dismissed"
    with pytest.raises(HTTPException):
        await routes.start_installation(org, row.id, cast(HumanPrincipal, principal), cast(AsyncSession, session))
    session.commit.assert_not_awaited()


async def test_installation_callback_only_returns_hint_without_connecting(configured, monkeypatch):
    row, org = connection_flow(), uuid4()
    onboarding = existing_onboarding(row, org)
    row.state_hash = digest("install-state")
    session = db(row)
    session.get.return_value = onboarding
    attach = AsyncMock()
    monkeypatch.setattr(routes, "connect_installation", attach)
    response = await routes.installation_callback(Request({"type":"http", "github_oauth_query":{"state":["install-state"],"installation_id":["8"]}}), cast(AsyncSession, session))
    assert "github_installation=" + str(row.id) in response.headers["location"]
    assert "github_installation_id=8" in response.headers["location"]
    assert "install-state" not in response.headers["location"]
    assert row.state_hash != digest("install-state")
    assert response.headers["cache-control"] == "no-store"
    attach.assert_not_awaited()


@pytest.mark.parametrize("status", ["dismissed", "expired", "connected"])
async def test_installation_return_cannot_revive_stopped_flow(configured, status):
    row = connection_flow()
    row.status = status
    with pytest.raises(HTTPException):
        await routes.installation_callback(Request({"type":"http", "github_oauth_query":{"state":["state"],"installation_id":["8"]}}), cast(AsyncSession, db(row)))


async def test_installation_callback_without_state_cannot_attach(configured):
    session = db()
    response = await routes.installation_callback(Request({"type":"http", "github_oauth_query":{"installation_id":["8"]}}), cast(AsyncSession, session))
    assert response.status_code == 303
    assert "github_installation" not in response.headers["location"]
    session.scalar.assert_not_awaited()


async def test_installation_callback_query_is_redacted():
    async def downstream(scope, receive, send):
        assert scope["query_string"] == b""
        assert scope["github_oauth_query"]["state"] == ["install-secret"]
    scope = {"type":"http", "path":"/api/v2/auth/github/installation/callback",
             "query_string":b"installation_id=8&state=install-secret"}
    await routes.GitHubOAuthQueryMiddleware(downstream)(scope, None, None)


@pytest.mark.parametrize("installation_id, available", [("9", True), ("88", False)])
async def test_returned_organization_installation_is_verified_before_automatic_attach(configured, monkeypatch, installation_id, available):
    row, org = connection_flow(), uuid4()
    session = db(row)
    provider = SimpleNamespace(api=AsyncMock(return_value=SimpleNamespace(data={"installations":[
        {"id":9,"app_id":77,"account":{"id":999,"type":"Organization","login":"team"}}
    ]})))
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: provider)
    attach = AsyncMock(return_value={"connectionId":"connection"})
    monkeypatch.setattr(routes, "connect_installation", attach)
    if available:
        result = await routes.continue_connection(org, row.id, cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})), cast(AsyncSession, session), routes.ConnectionContinuation(installation_id=installation_id))
        assert result["status"] == "connected"
        assert attach.await_args is not None
        assert attach.await_args.args[1] == installation_id
    else:
        with pytest.raises(HTTPException) as error:
            await routes.continue_connection(org, row.id, cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})), cast(AsyncSession, session), routes.ConnectionContinuation(installation_id=installation_id))
        assert error.value.status_code == 409
        attach.assert_not_awaited()


async def test_dismissal_during_discovery_prevents_attachment(configured, monkeypatch):
    row, org = connection_flow(), uuid4()
    session = db(row)
    async def discover(*args):
        row.status = "dismissed"
        return SimpleNamespace(data={"installations":[{"id":8,"app_id":77,"account":{"id":123,"type":"User"}}]})
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: SimpleNamespace(api=discover))
    attach = AsyncMock()
    monkeypatch.setattr(routes, "connect_installation", attach)
    with pytest.raises(HTTPException):
        await routes.continue_connection(org, row.id, cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})), cast(AsyncSession, session))
    attach.assert_not_awaited()


async def test_real_identity_map_refreshes_concurrent_dismissal(configured, monkeypatch):
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import Session

    from app.infrastructure.database.models import GitHubAuthFlow

    engine = create_engine("sqlite://")
    cast(Table, GitHubOnboarding.__table__).create(engine)
    cast(Table, GitHubAuthFlow.__table__).create(engine)
    org, user_id = uuid4(), uuid4()
    with Session(engine, expire_on_commit=False) as sync:
        onboarding = GitHubOnboarding(id=uuid4(), organization_id=org, owner_id=user_id,
            state_hash=digest("onboarding"), status="select_installation",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            credential_ciphertext=encrypt_integration_secret(encode_bundle(CredentialBundle(access_token="private-token", account_id="123"))))
        row = GitHubAuthFlow(id=uuid4(), user_id=user_id, onboarding_id=onboarding.id,
            purpose="signin", status="connector_pending", state_hash=digest("flow"),
            browser_challenge=digest("browser"), expires_at=onboarding.expires_at)
        sync.add_all([onboarding, row]);sync.commit()
        class Bridge:
            async def scalar(self, statement): return sync.scalar(statement)
            async def get(self, model, key, **kwargs): return sync.get(model, key, **kwargs)
            async def commit(self): sync.commit()
        async def discover(*args):
            with Session(engine) as other:
                other.execute(update(GitHubAuthFlow).where(GitHubAuthFlow.id == row.id).values(status="dismissed"))
                other.commit()
            assert row.status == "connector_pending", "The first session really holds a stale identity"
            return SimpleNamespace(data={"installations":[{"id":8,"app_id":77,"account":{"id":123,"type":"User"}}]})
        monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: SimpleNamespace(api=discover))
        attach = AsyncMock()
        monkeypatch.setattr(routes, "connect_installation", attach)
        with pytest.raises(HTTPException) as error:
            await routes.attach_signup_connection(org, row.id, SimpleNamespace(user_id=user_id, permissions={"integrations.manage"}), Bridge())
        assert error.value.status_code == 400 and row.status == "dismissed"
        attach.assert_not_awaited()
    engine.dispose()


@pytest.mark.parametrize("app_id, suspended", [(78, None), (77, "now")])
async def test_returned_installation_never_bypasses_app_or_suspension(configured, monkeypatch, app_id, suspended):
    row, org = connection_flow(), uuid4()
    provider = SimpleNamespace(api=AsyncMock(return_value=SimpleNamespace(data={"installations":[
        {"id":8,"app_id":app_id,"suspended_at":suspended,"account":{"id":123,"type":"User"}}
    ]})))
    monkeypatch.setattr(routes, "ExpandedGitHubProvider", lambda: provider)
    attach = AsyncMock()
    monkeypatch.setattr(routes, "connect_installation", attach)
    with pytest.raises(HTTPException) as error:
        await routes.continue_connection(org, row.id, cast(HumanPrincipal, SimpleNamespace(user_id=row.user_id, permissions={"integrations.manage"})), cast(AsyncSession, db(row)), routes.ConnectionContinuation(installation_id="8"))
    assert error.value.status_code == 409
    attach.assert_not_awaited()


async def test_disabled_connector_cannot_start_installation(configured, monkeypatch):
    monkeypatch.setattr(settings, "github_expanded_enabled", False)
    session = db()
    with pytest.raises(HTTPException) as error:
        await routes.start_installation(uuid4(), uuid4(), cast(HumanPrincipal, SimpleNamespace(permissions={"integrations.manage"})), cast(AsyncSession, session))
    assert error.value.status_code == 409
    session.scalar.assert_not_awaited()
