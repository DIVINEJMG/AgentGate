from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ObjectReference:
    key: str
    media_type: str
    size_bytes: int


class ObjectStorage(Protocol):
    async def put(self, *, key: str, content: bytes, media_type: str) -> ObjectReference: ...
    async def get(self, *, key: str) -> bytes: ...
    async def delete(self, *, key: str) -> None: ...
    async def signed_url(self, *, key: str, expires_seconds: int = 900) -> str: ...
