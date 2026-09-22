from uuid import UUID

from app.domain.artifacts.storage import ObjectReference, ObjectStorage


def tenant_storage_key(organization_id: UUID, key: str) -> str:
    normalized = key.strip().replace("\\", "/")
    segments = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or any(segment in {"", ".", ".."} for segment in segments)
    ):
        raise ValueError("Unsafe artifact storage key.")
    return f"org/{organization_id}/{normalized}"


class TenantObjectStorage:
    def __init__(self, storage: ObjectStorage, organization_id: UUID) -> None:
        self._storage = storage
        self._organization_id = organization_id

    def _key(self, key: str) -> str:
        return tenant_storage_key(self._organization_id, key)

    async def put(self, *, key: str, content: bytes, media_type: str) -> ObjectReference:
        return await self._storage.put(
            key=self._key(key),
            content=content,
            media_type=media_type,
        )

    async def get(self, *, key: str) -> bytes:
        return await self._storage.get(key=self._key(key))

    async def delete(self, *, key: str) -> None:
        await self._storage.delete(key=self._key(key))

    async def signed_url(self, *, key: str, expires_seconds: int = 900) -> str:
        return await self._storage.signed_url(
            key=self._key(key),
            expires_seconds=expires_seconds,
        )
