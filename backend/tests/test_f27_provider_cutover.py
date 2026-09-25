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

from unittest.mock import AsyncMock, MagicMock

from app.domain.jobs.dispatch import ScheduledDispatch
from app.infrastructure.database.dispatch import WorkItemDispatchRepository
from app.infrastructure.qstash.provider import (
    UpstashQStashProvider,
    _deduplication_id,
    _destination_path,
)
from app.infrastructure.storage.upstash_blob import UpstashBlobObjectStorage


@pytest.mark.asyncio
async def test_upstash_blob_adapter_delegates_without_mutating_bytes() -> None:
    transport = AsyncMock()
    transport.get.return_value = b"payload"
    transport.signed_url.return_value = "https://blob.invalid/signed"
    storage = UpstashBlobObjectStorage(transport)

    ref = await storage.put(key="org/x/report.bin", content=b"payload", media_type="application/octet-stream")
    assert ref.key == "org/x/report.bin"
    assert ref.size_bytes == 7
    assert await storage.get(key=ref.key) == b"payload"
    assert await storage.signed_url(key=ref.key, expires_seconds=60) == "https://blob.invalid/signed"
    await storage.delete(key=ref.key)
    transport.put.assert_awaited_once_with(key=ref.key, content=b"payload", media_type="application/octet-stream")


def test_qstash_headers_include_retry_timeout_and_deduplication() -> None:
    provider = UpstashQStashProvider(base_url="https://qstash.example", token="secret")
    headers = provider._headers(retries=4, timeout_seconds=20, idempotency_key="idem-123")
    assert headers["Upstash-Retries"] == "4"
    assert headers["Upstash-Timeout"] == "20s"
    assert headers["Upstash-Deduplication-Id"] == "idem-123"
    assert headers["Authorization"].startswith("Bearer ")


def test_qstash_destination_path_preserves_url_scheme_and_slashes() -> None:
    destination = "https://audoryn.example/internal/v1/runtime/execute?step=1"
    encoded = _destination_path(destination)
    assert encoded.startswith("https://audoryn.example/internal/v1/runtime/execute")
    assert "%3Fstep%3D1" in encoded
    assert "https%3A%2F%2F" not in encoded


def test_qstash_deduplication_id_hashes_internal_delimiters() -> None:
    raw = "runtime:00000000-0000-0000-0000-000000000001:0:recovery"
    deduplication_id = _deduplication_id(raw)
    assert deduplication_id.startswith("audoryn-")
    assert ":" not in deduplication_id
    assert deduplication_id == _deduplication_id(raw)
    assert deduplication_id != _deduplication_id(raw + ":next")


@pytest.mark.asyncio
async def test_dispatch_repository_returns_existing_work_item_for_duplicate_key() -> None:
    existing = MagicMock()
    session = AsyncMock()
    session.scalar.return_value = existing
    repo = WorkItemDispatchRepository(session)
    dispatch = ScheduledDispatch(
        organization_id=uuid4(),
        job_id=uuid4(),
        job_revision_id=uuid4(),
        idempotency_key="same-key",
        correlation_id="corr-1",
        scheduled_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        payload={},
    )
    result = await repo.create(dispatch)
    assert result is existing
    session.add.assert_not_called()
