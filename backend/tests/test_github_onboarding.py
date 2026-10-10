from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import github_routes
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.execution.providers.integration_hooks import CredentialBundle
from app.execution.providers.native.github.authentication import GitHubAuthentication
from app.execution.providers.native.http import ProviderTransportError


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "github_expanded_enabled", True)
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    monkeypatch.setattr(settings, "integration_encryption_key", SecretStr("test-vault-key"))
    monkeypatch.setattr(settings, "github_client_secret", SecretStr("test-client-secret"))


async def test_missing_vault_blocks_callback_before_code_exchange(configured, monkeypatch):
    monkeypatch.setattr(settings, "integration_encryption_key", None)
    session = SimpleNamespace(scalar=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await github_routes.callback("one-use-code", "state", cast(AsyncSession, session))
    assert error.value.status_code == 409
    assert "INTEGRATION_ENCRYPTION_KEY" in error.value.detail
    session.scalar.assert_not_awaited()


async def test_readiness_requires_vault(configured, monkeypatch):
    for name in ("github_app_id", "github_client_id", "github_callback_url"):
        monkeypatch.setattr(settings, name, "configured")
    monkeypatch.setattr(settings, "github_app_private_key", SecretStr("configured"))
    principal = cast(HumanPrincipal, SimpleNamespace(permissions={"integrations.read"}, user_id=uuid4()))
    assert (await github_routes.readiness(uuid4(), principal, cast(AsyncSession, SimpleNamespace(scalar=AsyncMock(return_value=None)))))["authenticationConfigured"]
    monkeypatch.setattr(settings, "integration_encryption_key", None)
    result = await github_routes.readiness(uuid4(), principal, cast(AsyncSession, SimpleNamespace(scalar=AsyncMock(return_value=None))))
    assert not result["authenticationConfigured"]
    assert not result["credentialVaultConfigured"]


@pytest.mark.parametrize("retryable, expected", [(False, 400), (True, 503)])
async def test_callback_provider_failure_consumes_state_safely(
    configured, monkeypatch, retryable, expected
):
    row = SimpleNamespace(
        status="authorizing", expires_at=datetime.now(UTC) + timedelta(minutes=10),
        organization_id=uuid4(), owner_id=uuid4(), credential_ciphertext=None,
    )
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[row, uuid4()]), commit=AsyncMock())
    provider = SimpleNamespace(complete_authorization=AsyncMock(side_effect=ProviderTransportError(
        code="authentication_error", retryable=retryable, safe_message="GitHub unavailable."
    )))
    monkeypatch.setattr(github_routes, "ExpandedGitHubProvider", lambda: provider)
    with pytest.raises(HTTPException) as error:
        await github_routes.callback("private-code", "private-state", cast(AsyncSession, session))
    assert error.value.status_code == expected
    assert "private-code" not in error.value.detail
    assert row.status == "failed"
    assert row.credential_ciphertext is None
    session.commit.assert_awaited_once()


async def test_callback_encrypts_and_redirects_without_token(configured, monkeypatch):
    row = SimpleNamespace(
        id=uuid4(), status="authorizing", expires_at=datetime.now(UTC) + timedelta(minutes=10),
        organization_id=uuid4(), owner_id=uuid4(), credential_ciphertext=None,
    )
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[row, uuid4()]), commit=AsyncMock())
    provider = SimpleNamespace(complete_authorization=AsyncMock(return_value=CredentialBundle(
        access_token="private-token", account_id="account"
    )))
    monkeypatch.setattr(github_routes, "ExpandedGitHubProvider", lambda: provider)
    response = await github_routes.callback("code", "state", cast(AsyncSession, session))
    assert response.status_code == 303
    assert "private-token" not in response.headers["location"]
    assert "private-token" not in row.credential_ciphertext
    assert row.status == "select_installation"
    session.commit.assert_awaited_once()


async def test_oauth_network_failure_is_classified(configured, monkeypatch):
    client = SimpleNamespace(post=AsyncMock(side_effect=httpx.ConnectError("private-detail")))
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: context)
    with pytest.raises(ProviderTransportError) as error:
        await GitHubAuthentication(None)._oauth({"code": "private-code"})
    assert error.value.retryable
    assert "private" not in error.value.safe_message


@pytest.mark.parametrize("linked", [False, True])
async def test_readiness_reports_current_users_login_mapping(configured, monkeypatch, linked):
    monkeypatch.setattr(settings, "github_login_enabled", True)
    user_id = uuid4()
    principal = cast(HumanPrincipal, SimpleNamespace(permissions={"integrations.read"}, user_id=user_id))
    session = SimpleNamespace(scalar=AsyncMock(return_value=uuid4() if linked else None))
    result = await github_routes.readiness(uuid4(), principal, cast(AsyncSession, session))
    assert result["loginEnabled"] is True
    assert result["signInLinked"] is linked
    query = session.scalar.await_args.args[0]
    assert user_id in query.compile().params.values()
    assert "github" in query.compile().params.values()


async def test_disabled_login_readiness_does_not_query_new_tables(configured, monkeypatch):
    monkeypatch.setattr(settings, "github_login_enabled", False)
    principal = cast(HumanPrincipal, SimpleNamespace(permissions={"integrations.read"}))
    session = SimpleNamespace(scalar=AsyncMock())
    result = await github_routes.readiness(uuid4(), principal, cast(AsyncSession, session))
    assert result["signInLinked"] is False
    session.scalar.assert_not_awaited()


async def test_installation_diagnostics_distinguish_empty_access(configured, monkeypatch, caplog):
    import logging

    principal = cast(HumanPrincipal, SimpleNamespace(permissions={"integrations.manage"}, user_id=uuid4()))
    bundle = CredentialBundle(access_token="private-token", account_id="123")
    monkeypatch.setattr(github_routes, "pending", AsyncMock(return_value=(None, bundle)))
    provider = SimpleNamespace(api=AsyncMock(return_value=SimpleNamespace(data={"installations": []})))
    monkeypatch.setattr(github_routes, "ExpandedGitHubProvider", lambda: provider)
    with caplog.at_level(logging.INFO):
        result = await github_routes.installations(uuid4(), uuid4(), principal, cast(AsyncSession, SimpleNamespace()))
    assert result == {"installations": []}
    assert "matching=0 usable=0" in caplog.text
    assert "private-token" not in caplog.text
