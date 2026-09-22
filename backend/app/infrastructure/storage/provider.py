from app.domain.artifacts.storage import ObjectReference, ObjectStorage


class UnconfiguredObjectStorage(ObjectStorage):
    """Fail closed until Upstash Blob or another ObjectStorage provider is configured."""

    async def put(self, *, key: str, content: bytes, media_type: str) -> ObjectReference:
        raise RuntimeError("Object storage provider is not configured.")

    async def get(self, *, key: str) -> bytes:
        raise RuntimeError("Object storage provider is not configured.")

    async def delete(self, *, key: str) -> None:
        raise RuntimeError("Object storage provider is not configured.")

    async def signed_url(self, *, key: str, expires_seconds: int = 900) -> str:
        raise RuntimeError("Object storage provider is not configured.")
