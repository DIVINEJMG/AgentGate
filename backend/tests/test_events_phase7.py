"""Improvement plan, phase 7: one outbox drain request at a time, maintenance on its own
schedule, and live events written once. Whole-task savings are in the offline benchmark."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from redis.exceptions import RedisError

from audoryn_execution.api.internal import outbox as outbox_routes
from audoryn_execution.api.internal import runtime as runtime_routes
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.infrastructure.qstash.provider import UpstashQStashProvider
from audoryn_shared.infrastructure.redis.coordination import RedisCoordinator
from audoryn_shared.realtime.bus import RedisRealtimeBus
from audoryn_shared.realtime.contracts import RealtimeEvent
from audoryn_shared.runtime import outbox_publisher, outbox_trigger
from scripts.runtime_benchmark import FakeCoordinator, FakeQStash, FakeRequest
from tests.test_f29_31_40 import FakeRedis


@pytest.fixture
def drains(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeQStash, dict[str, Any]]:
    qstash, state = FakeQStash(), {}
    monkeypatch.setattr(UpstashQStashProvider, "from_settings", classmethod(lambda cls: qstash))
    monkeypatch.setattr(RedisCoordinator, "from_settings", classmethod(lambda cls: FakeCoordinator(state)))
    for name, value in {"qstash_url": "https://qstash.test", "qstash_token": SecretStr("t"),
                        "qstash_outbox_drain_url": "https://app.test/internal/v1/outbox/drain"}.items():
        monkeypatch.setattr(settings, name, value)
    return qstash, state


@pytest.mark.asyncio
async def test_commits_while_a_drain_is_on_its_way_do_not_send_another(drains: tuple[FakeQStash, dict[str, Any]]) -> None:
    qstash, _ = drains
    results = [await outbox_trigger.request_outbox_drain(reason="transaction_committed") for _ in range(3)]
    assert results[0] == "bench-1" and results[1:] == [outbox_trigger.COALESCED] * 2
    assert len(qstash.queue) == 1


@pytest.mark.asyncio
async def test_the_drain_clears_the_marker_before_it_reads_so_nothing_is_missed(
    drains: tuple[FakeQStash, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    qstash, state = drains
    await outbox_trigger.request_outbox_drain(reason="transaction_committed")  # commit A
    await outbox_trigger.request_outbox_drain(reason="transaction_committed")  # commit B relies on it
    marker_when_reading: list[bool] = []

    async def drain(*, limit: int) -> int:
        marker_when_reading.append(f"flag:{outbox_trigger.DRAIN_PENDING}" in state)
        return 2

    monkeypatch.setattr(outbox_routes, "drain_outbox_batch", drain)
    monkeypatch.setattr(outbox_routes, "_verify_qstash", lambda *a: None)
    await outbox_routes.drain(FakeRequest("{}"), "sig")  # type: ignore[arg-type]
    # Both commits were before the marker was cleared, so before this read: it sees both rows.
    assert marker_when_reading == [False]
    assert await outbox_trigger.request_outbox_drain(reason="transaction_committed") == "bench-2"  # a later commit asks again
    assert len(qstash.queue) == 2


@pytest.mark.asyncio
async def test_a_failed_request_lets_the_next_commit_ask_again(drains: tuple[FakeQStash, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    qstash, state = drains
    monkeypatch.setattr(qstash, "publish", AsyncMock(side_effect=RuntimeError("QStash unavailable")))
    with pytest.raises(RuntimeError):
        await outbox_trigger.request_outbox_drain(reason="transaction_committed")
    assert f"flag:{outbox_trigger.DRAIN_PENDING}" not in state


@pytest.mark.asyncio
async def test_without_redis_every_commit_asks_for_its_own_drain_as_before(
    drains: tuple[FakeQStash, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    qstash, _ = drains
    monkeypatch.setattr(FakeCoordinator, "claim_flag", AsyncMock(side_effect=RedisError("down")))
    for _ in range(2):
        assert (await outbox_trigger.request_outbox_drain(reason="transaction_committed")) not in (None, outbox_trigger.COALESCED)
    assert len(qstash.queue) == 2


@pytest.mark.asyncio
async def test_maintenance_runs_on_the_sweep_not_on_every_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    maintenance = AsyncMock()
    monkeypatch.setattr(outbox_publisher, "run_periodic_maintenance", maintenance)
    monkeypatch.setattr(settings, "coding_execution_enabled", True)
    coding = AsyncMock()
    monkeypatch.setattr("audoryn_shared.execution.coding.service.maintain_coding_sessions", coding)

    class Empty:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(outbox_publisher, "session_factory", lambda: Empty())
    monkeypatch.setattr(outbox_publisher, "TransactionalOutbox", lambda session: AsyncMock(unpublished=AsyncMock(return_value=[])))
    assert await outbox_publisher.drain_outbox_batch() == 0
    coding.assert_not_awaited()

    monkeypatch.setattr(runtime_routes, "_verify_qstash", lambda *a: None)
    monkeypatch.setattr(settings, "runtime_execution_enabled", False)  # it runs even then
    await runtime_routes.sweep(FakeRequest("{}"), "sig")  # type: ignore[arg-type]
    maintenance.assert_awaited_once()


class CountingRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list[str] = []

    async def set(self, key: Any, value: Any, nx: bool = False, ex: Any = None) -> Any:
        self.commands.append("set")
        return await super().set(key, value, nx=nx, ex=ex)

    async def get(self, key: Any) -> Any:
        self.commands.append("get")
        return await super().get(key)

    async def delete(self, key: Any) -> Any:
        self.commands.append("delete")
        return await super().delete(key)

    async def xadd(self, key: Any, fields: Any, **kwargs: Any) -> Any:
        self.commands.append("xadd")
        return await super().xadd(key, fields, **kwargs)

    async def publish(self, channel: Any, payload: Any) -> Any:
        self.commands.append("publish")
        return await super().publish(channel, payload)


@pytest.mark.asyncio
async def test_a_live_event_is_written_once() -> None:
    redis = CountingRedis()
    bus = RedisRealtimeBus(redis)  # type: ignore[arg-type]
    await bus.emit(RealtimeEvent(event_type="run.progress", organization_id=uuid4(), worker_id=uuid4(), run_id=uuid4(),
                                 payload={}))
    # Claim, write, notify. Before: claim, three stream writes, a second set, notify.
    assert redis.commands == ["set", "xadd", "publish"]


@pytest.mark.asyncio
async def test_readers_of_one_run_filter_the_organisation_stream_and_never_stall() -> None:
    bus = RedisRealtimeBus(FakeRedis())  # type: ignore[arg-type]
    organization, mine, other = uuid4(), uuid4(), uuid4()
    for run in (mine, other, other, mine, other):
        await bus.emit(RealtimeEvent(event_type="run.progress", organization_id=organization, run_id=run, payload={}))
    events, cursor = await bus.read_page(organization_id=organization, count=2, run_id=mine)
    assert [event["run_id"] for event in events] == [str(mine)] and cursor == "2-0"
    events, cursor = await bus.read_page(organization_id=organization, after=cursor, count=1, run_id=mine)
    assert events == [] and cursor == "3-0"  # nothing for this run here, but the position still moves
    events, cursor = await bus.read_page(organization_id=organization, after=cursor, count=10, run_id=mine)
    assert [event["stream_id"] for event in events] == ["4-0"] and cursor == "5-0"


@pytest.mark.asyncio
async def test_a_drain_request_started_after_a_commit_is_kept_until_it_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Live test: the request was garbage-collected mid-flight ("Task was destroyed but it is
    # pending") after claiming the drain marker, so a queued task never started.
    import asyncio
    import gc
    release = asyncio.Event()

    async def slow_request(*, reason: str) -> str:
        await release.wait()
        return "sent"

    monkeypatch.setattr(outbox_trigger, "request_outbox_drain", slow_request)
    outbox_trigger.request_outbox_drain_after_commit(reason="transaction_committed")
    gc.collect()
    [task] = list(outbox_trigger._pending)
    assert not task.done()
    release.set()
    assert await task == "sent"
    await asyncio.sleep(0)
    assert not outbox_trigger._pending
