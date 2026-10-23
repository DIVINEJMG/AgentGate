"""Several backend copies (architecture plan, Phase 2): the background loops run in every copy and
share the same pending work, so each piece of it must be done by one copy only."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from audoryn_execution.application.services import approval_expiry
from audoryn_execution.runtime import preparation_worker as worker
from audoryn_shared.infrastructure.redis.coordination import Lease


class SharedLocks:
    """The Redis both copies see: a lock is held until released."""

    def __init__(self) -> None:
        self.held: dict[str, str] = {}

    def __call__(self) -> SharedLocks:  # stands in for preparation_worker._coordinator
        return self

    async def acquire_lock(self, key: str, *, ttl_seconds: int) -> Lease | None:
        if key in self.held:
            return None
        self.held[key] = token = uuid4().hex
        return Lease(key, token)

    async def release_lock(self, lease: Lease) -> bool:
        return self.held.pop(lease.key, None) == lease.token

    async def renew_lock(self, lease: Lease, *, ttl_seconds: int) -> bool:
        return self.held.get(lease.key) == lease.token

    async def close(self) -> None:
        return None


@pytest.fixture
def locks(monkeypatch: pytest.MonkeyPatch) -> SharedLocks:
    shared = SharedLocks()
    monkeypatch.setattr(worker, "_coordinator", shared)
    monkeypatch.setattr(worker, "record_usage", lambda *args, **kwargs: asyncio.sleep(0))
    return shared


@pytest.mark.asyncio
async def test_a_preparation_runs_in_one_copy_while_the_other_recovers_it(locks: SharedLocks,
                                                                          monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    release = asyncio.Event()

    async def prepare(command_id: Any) -> None:
        calls.append(command_id)
        await release.wait()

    wakes: list[bool] = []
    monkeypatch.setattr(worker, "_recovery_wake", type("Wake", (), {"set": lambda self: wakes.append(True)})())
    command_id = uuid4()
    first = asyncio.create_task(worker._metered(prepare, command_id, kind="preparation.task"))
    await asyncio.sleep(0)
    # The other copy's recovery pass finds the same pending command.
    second = asyncio.create_task(worker._metered(prepare, command_id, kind="preparation.task"))
    assert await second is False
    worker._wake_recovery(second)  # it ended at once, so it must not wake recovery (that would spin)
    assert calls == [command_id] and wakes == []
    release.set()
    await first
    assert locks.held == {}  # released: the next preparation of it can run anywhere
    worker._wake_recovery(first)
    assert wakes == [True]


@pytest.mark.asyncio
async def test_a_lost_lease_stops_the_preparation_before_another_copy_takes_over(locks: SharedLocks,
                                                                                monkeypatch: pytest.MonkeyPatch):
    started = asyncio.Event()

    async def prepare(command_id: Any) -> None:
        started.set()
        await asyncio.Event().wait()

    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: real_sleep(0))  # renew at once
    task = asyncio.create_task(worker._metered(prepare, uuid4(), kind="preparation.task"))
    await started.wait()
    locks.held.clear()  # the lease lapsed (this copy stalled) and another copy may now take it
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_without_redis_the_preparation_waits_for_the_next_recovery_pass(monkeypatch: pytest.MonkeyPatch):
    class Down(SharedLocks):
        async def acquire_lock(self, key: str, *, ttl_seconds: int) -> Lease | None:
            raise ConnectionError("redis is down")

    async def prepare(command_id: Any) -> None:
        raise AssertionError("it must not run without its lease")

    monkeypatch.setattr(worker, "_coordinator", Down())
    assert await worker._metered(prepare, uuid4(), kind="preparation.task") is False


@pytest.mark.asyncio
async def test_the_approval_sweep_skips_rows_another_copy_holds():
    statements: list[Any] = []

    class Session:
        async def execute(self, statement: Any) -> Any:
            statements.append(statement)
            return type("Result", (), {"all": lambda self: []})()

    assert await approval_expiry.expire_and_remind(cast(Any, Session()), now=datetime.now(UTC)) == []
    sql = str(statements[0].compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    assert sql.endswith("FOR UPDATE SKIP LOCKED")
