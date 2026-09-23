from typing import ClassVar

from pydantic import SecretStr

from app.bootstrap.settings import settings
from app.domain.secrets.vault import SecretVault


class SettingsSecretVault(SecretVault):
    """Explicit server-side secret aliases. Values never cross API boundaries."""

    _ALIASES: ClassVar[dict[str, str]] = {
        "integration-encryption-key": "integration_encryption_key",
        "oidc-client-secret": "oidc_client_secret",
        "model-provider-api-key": "model_provider_api_key",
        "object-storage-access-key": "object_storage_access_key",
        "object-storage-secret-key": "object_storage_secret_key",
        "qstash-token": "qstash_token",
        "upstash-blob-token": "upstash_blob_token",
    }

    async def read(self, secret_id: str) -> str:
        attribute = self._ALIASES.get(secret_id)
        if attribute is None:
            raise KeyError(f"Unknown secret reference: {secret_id}")
        value = getattr(settings, attribute)
        if value is None:
            raise RuntimeError(f"Secret is not configured: {secret_id}")
        if not isinstance(value, SecretStr):
            raise TypeError(f"Configured secret has invalid type: {secret_id}")
        return value.get_secret_value()
