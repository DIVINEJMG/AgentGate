"""Provider-edge credential renewal; never expose the bundle to planning."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from redis.exceptions import RedisError
from sqlalchemy import select

from app.execution.contracts import ExecutionProviderError
from app.execution.providers.integration_hooks import AuthenticationHooks, CredentialBundle
from app.infrastructure.database.models import IntegrationConnectionState, IntegrationCredential
from app.infrastructure.redis.coordination import RedisCoordinator
from app.infrastructure.secrets.integration_crypto import (
    decrypt_integration_secret,
    encrypt_integration_secret,
)

logger = logging.getLogger(__name__)


def encode_bundle(bundle: CredentialBundle) -> str:
    return json.dumps({"format": "audoryn-credential-v1", "access_token": bundle.access_token,
        "refresh_token": bundle.refresh_token, "expires_at": bundle.expires_at.isoformat() if bundle.expires_at else None,
        "scopes": list(bundle.scopes), "renewal_metadata": bundle.renewal_metadata, "account_id": bundle.account_id})


def decode_bundle(value: str) -> CredentialBundle:
    try:
        raw = json.loads(value)
    except ValueError:
        raw = None
    if not isinstance(raw, dict) or raw.get("format") != "audoryn-credential-v1":
        return CredentialBundle(access_token=value)
    return CredentialBundle(access_token=raw["access_token"], refresh_token=raw.get("refresh_token"),
        expires_at=datetime.fromisoformat(raw["expires_at"]) if raw.get("expires_at") else None,
        scopes=tuple(raw.get("scopes", [])), renewal_metadata=raw.get("renewal_metadata", {}), account_id=raw.get("account_id"))


class CredentialManager:
    def __init__(self, session, coordinator=None):
        self.session = session
        self.coordinator = coordinator

    async def resolve(self, *, connection, provider, correlation_id: str) -> str | None:
        state = await self.session.scalar(select(IntegrationConnectionState).where(
            IntegrationConnectionState.connection_id == connection.id))
        row = await self.session.scalar(select(IntegrationCredential).where(
            IntegrationCredential.integration_id == connection.id))
        def reconnect(reason):
            if state:
                state.authorization_state, state.reason = "reconnect_required", reason
            return ExecutionProviderError(code="authentication_error", retryable=False,
                provider=connection.provider, operation="credential.resolve", correlation_id=correlation_id,
                safe_message=reason)
        if state and state.authorization_state in {"reconnect_required", "disconnected"}:
            raise reconnect("Reconnect this integration account to continue the pending action.")
        if row is None:
            if not any(cap.requires_credential for cap in provider.manifest.capabilities):
                return None
            # Public read capabilities (such as GitHub metadata) can use no token;
            # the Action Gateway still rejects credential-required capabilities.
            if any(not cap.requires_credential for cap in provider.manifest.capabilities):
                return None
            raise reconnect("This integration account has no credential. Reconnect it to continue.")
        bundle = decode_bundle(decrypt_integration_secret(row.ciphertext))
        if state and bundle.scopes:
            state.credential_metadata = {**state.credential_metadata, "scopes": list(bundle.scopes)}
        if not bundle.expires_at or bundle.expires_at > datetime.now(UTC) + timedelta(seconds=30):
            return bundle.access_token
        from app.execution.providers.integration_hooks import CredentialRenewalHooks
        renewable = bool(bundle.refresh_token) or (isinstance(provider, CredentialRenewalHooks)
            and provider.can_renew_credentials(bundle=bundle))
        if not isinstance(provider, AuthenticationHooks) or not renewable:
            raise reconnect("This credential has expired. Its provider requires reconnection.")
        coordinator = self.coordinator or RedisCoordinator.from_settings()
        lease = None
        try:
            lease = await coordinator.acquire_lock(f"integration-renew:{connection.id}", ttl_seconds=60)
            if lease is None:
                raise ExecutionProviderError(code="rate_limited", retryable=True, provider=connection.provider,
                    operation="credential.renew", correlation_id=correlation_id,
                    safe_message="Credential renewal is already in progress; this action will wait.")
            # Row lock and refresh defeat stale in-process bundles after another worker renewed.
            row = await self.session.scalar(select(IntegrationCredential).where(
                IntegrationCredential.integration_id == connection.id).with_for_update().execution_options(populate_existing=True))
            bundle = decode_bundle(decrypt_integration_secret(row.ciphertext))
            if bundle.expires_at and bundle.expires_at > datetime.now(UTC) + timedelta(seconds=30):
                return bundle.access_token
            try:
                async with asyncio.timeout(30):
                    renewed = await provider.renew_credentials(bundle=bundle)
            except ExecutionProviderError as error:
                if error.error.code in {"authentication_error", "authorization_error"}:
                    raise reconnect("The provider rejected credential renewal. Reconnect this account.") from error
                raise
            if state:
                if renewed.account_id and state.account_id and renewed.account_id != state.account_id:
                    raise reconnect("Credential renewal returned a different account. Reconnect this account.")
                state.account_id = renewed.account_id or state.account_id
                state.credential_metadata = {"expiresAt": renewed.expires_at.isoformat() if renewed.expires_at else None,
                    "scopes": list(renewed.scopes), "rotatedAt": datetime.now(UTC).isoformat()}
            row.ciphertext = encrypt_integration_secret(encode_bundle(renewed))
            await self.session.flush()
            return renewed.access_token
        except (TimeoutError, RedisError) as error:
            raise ExecutionProviderError(code="temporary_provider_error", retryable=True,
                provider=connection.provider, operation="credential.renew", correlation_id=correlation_id,
                safe_message="Credential renewal is temporarily unavailable; the pending action will wait.") from error
        finally:
            try:
                if lease:
                    await coordinator.release_lock(lease)
                if self.coordinator is None:
                    await coordinator.close()
            except RedisError:
                logger.warning("Credential coordination cleanup could not complete.")
