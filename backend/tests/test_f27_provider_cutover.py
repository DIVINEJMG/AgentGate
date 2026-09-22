from uuid import uuid4

import pytest

from app.domain.artifacts.storage import ObjectReference
from app.domain.artifacts.tenant_storage import TenantObjectStorage, tenant_storage_key


class RecordingStorage:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def put(self, *, key: str, content: bytes, media_type: str) -> ObjectReference:
        self.keys.append(key)
        return ObjectReference(key=key, media_type=media_type, size_bytes=len(content))

    async def get(self, *, key: str) -> bytes:
        self.keys.append(key)
        return b"payload"

    async def delete(self, *, key: str) -> None:
        self.keys.append(key)

    async def signed_url(self, *, key: str, expires_seconds: int = 900) -> str:
        self.keys.append(key)
        return f"https://example.invalid/{key}?expires={expires_seconds}"


def test_tenant_storage_key_isolated_by_organization() -> None:
    first = uuid4()
    second = uuid4()
    assert tenant_storage_key(first, "results/report.pdf").startswith(f"org/{first}/")
    assert tenant_storage_key(second, "results/report.pdf").startswith(f"org/{second}/")
    assert tenant_storage_key(first, "results/report.pdf") != tenant_storage_key(
        second, "results/report.pdf"
    )


@pytest.mark.parametrize("key", ["../secret", "/absolute", "a/../b", "a//b"])
def test_tenant_storage_key_rejects_traversal(key: str) -> None:
    with pytest.raises(ValueError):
        tenant_storage_key(uuid4(), key)


@pytest.mark.asyncio
async def test_tenant_storage_scopes_large_payload_without_changing_bytes() -> None:
    organization_id = uuid4()
    underlying = RecordingStorage()
    storage = TenantObjectStorage(underlying, organization_id)
    content = b"x" * (9 * 1024 * 1024)

    reference = await storage.put(
        key="exports/large.bin",
        content=content,
        media_type="application/octet-stream",
    )

    assert reference.size_bytes == len(content)
    assert reference.key == f"org/{organization_id}/exports/large.bin"
    assert underlying.keys == [reference.key]
