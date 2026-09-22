from uuid import uuid4

import pytest

from app.api.adapters.system_status import serialize_v1, serialize_v2
from app.application.queries.system_status import get_system_status
from app.domain.artifacts.storage import ObjectStorage
from app.infrastructure.storage.provider import UnconfiguredObjectStorage
from app.runtime.executor import UnconfiguredRuntimeExecutor


def test_v1_and_v2_are_adapters_over_one_canonical_status() -> None:
    status = get_system_status()
    v1 = serialize_v1(status)
    v2 = serialize_v2(status)
    assert v1["currentVersion"] == status.current_version
    assert v2["lifecycle"]["current"] == status.current_version
    assert v1["supportedVersions"] == list(status.supported_versions)
    assert v2["lifecycle"]["supported"] == list(status.supported_versions)


@pytest.mark.asyncio
async def test_unconfigured_object_storage_fails_closed() -> None:
    storage: ObjectStorage = UnconfiguredObjectStorage()
    with pytest.raises(RuntimeError):
        await storage.put(key="artifact/test.txt", content=b"test", media_type="text/plain")


@pytest.mark.asyncio
async def test_runtime_executor_fails_closed_until_migration_is_enabled() -> None:
    executor = UnconfiguredRuntimeExecutor()
    from app.domain.jobs.queue import ClaimedWorkItem
    from datetime import UTC, datetime

    item = ClaimedWorkItem(
        id=uuid4(),
        organization_id=uuid4(),
        job_id=uuid4(),
        job_revision_id=uuid4(),
        correlation_id="corr-test",
        payload={},
        scheduled_at=datetime.now(UTC),
    )
    with pytest.raises(RuntimeError):
        await executor.execute(item)
