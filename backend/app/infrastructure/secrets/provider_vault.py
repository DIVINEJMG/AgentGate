from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import IntegrationCredential
from app.infrastructure.secrets.integration_crypto import (
    IntegrationCipherError,
    decrypt_integration_secret,
)


class DatabaseSecretVault:
    """Resolve opaque secret references only at the provider execution edge."""

    PREFIX = "secret://integration-credential/"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @classmethod
    def reference_for(cls, credential_id: UUID) -> str:
        return f"{cls.PREFIX}{credential_id}"

    async def read(self, secret_reference: str) -> str:
        if not secret_reference.startswith(self.PREFIX):
            raise LookupError("Unsupported provider credential reference.")
        raw_id = secret_reference.removeprefix(self.PREFIX).strip()
        try:
            credential_id = UUID(raw_id)
        except ValueError as error:
            raise LookupError("Provider credential reference is invalid.") from error

        row = await self._session.scalar(
            select(IntegrationCredential).where(IntegrationCredential.id == credential_id)
        )
        if row is None:
            raise LookupError("Provider credential reference was not found.")
        try:
            return decrypt_integration_secret(row.ciphertext)
        except IntegrationCipherError as error:
            raise LookupError("Integration credential is unavailable.") from error
