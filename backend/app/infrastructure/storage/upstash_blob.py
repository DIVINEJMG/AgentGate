from typing import Protocol

from app.domain.artifacts.storage import ObjectReference, ObjectStorage


class UpstashBlobTransport(Protocol):
    async def put(self, *, key: str, content: bytes, media_type: str) -> None: ...
    async def get(self, *, key: str) -> bytes: ...
    async def delete(self, *, key: str) -> None: ...
    async def signed_url(self, *, key: str, expires_seconds: int) -> str: ...


class UpstashBlobObjectStorage(ObjectStorage):
    """Provider adapter boundary for Upstash Blob."""

    def __init__(self, transport: UpstashBlobTransport) -> None:
        self._transport = transport

    async def put(self, *, key: str, content: bytes, media_type: str) -> ObjectReference:
        await self._transport.put(key=key, content=content, media_type=media_type)
        return ObjectReference(key=key, media_type=media_type, size_bytes=len(content))

    async def get(self, *, key: str) -> bytes:
        return await self._transport.get(key=key)

    async def delete(self, *, key: str) -> None:
        await self._transport.delete(key=key)

    async def signed_url(self, *, key: str, expires_seconds: int = 900) -> str:
        return await self._transport.signed_url(key=key, expires_seconds=expires_seconds)
