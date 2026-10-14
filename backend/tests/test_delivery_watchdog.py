"""The delivery watchdog: work whose wake-up was lost is recovered by the backend itself,
only after activity, so an idle backend makes no queries."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.api.internal import runtime as internal_runtime
from app.bootstrap.settings import settings
from app.infrastructure.database import models as m
from app.runtime import delivery_watchdog, outbox_publisher, outbox_trigger
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes
from tests.test_approvals_phase4 import TABLES
from tests.test_efficiency_queries import ORG, Database


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Any:
    db = Database(TABLES)
    monkeypatch.setattr(delivery_watchdog, "session_factory", lambda: BenchSession(Session(db.engine)))
    monkeypatch.setattr(settings, "delivery_watchdog_seconds", 180)
    monkeypatch.setattr(settings, "runtime_execution_enabled", True)
    drain = AsyncMock(return_value=1)
    sweep = AsyncMock(return_value={"queued": 1})
    monkeypatch.setattr(outbox_publisher, "drain_outbox_batch", drain)
    monkeypatch.setattr(internal_runtime, "run_sweep", sweep)
    return db, drain, sweep


def _outbox(db: Database, *, age: int) -> None:
    created = datetime.now(UTC) - timedelta(seconds=age)
    db.add(m.OutboxEvent, topic="run.progress", aggregate_type="work_item", aggregate_id=str(uuid4()), payload={},
           published_at=None, next_delivery_at=None, created_at=created, updated_at=created)


def _work(db: Database, status: str) -> None:
    db.add(m.WorkItem, organization_id=ORG, status=status, scheduled_at=datetime.now(UTC) - timedelta(minutes=5),
           correlation_id=str(uuid4()), payload={})


@pytest.mark.asyncio
async def test_a_lost_drain_request_is_published_by_the_watchdog(world: Any) -> None:
    db, drain, sweep = world
    _outbox(db, age=400)
    with _aware_sqlite_datetimes():
        assert await delivery_watchdog.recover_lost_deliveries() is True
    drain.assert_awaited_once()
    sweep.assert_not_awaited()


@pytest.mark.asyncio
async def test_rows_still_inside_their_normal_delivery_time_are_left_to_the_drain_request(world: Any) -> None:
    db, drain, sweep = world
    _outbox(db, age=30)
    with _aware_sqlite_datetimes():
        assert await delivery_watchdog.recover_lost_deliveries() is False
    drain.assert_not_awaited()
    sweep.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_work_gets_its_wake_up_sent_again(world: Any) -> None:
    db, drain, sweep = world
    _work(db, "queued")
    _work(db, "completed")
    with _aware_sqlite_datetimes():
        assert await delivery_watchdog.recover_lost_deliveries() is True
    sweep.assert_awaited_once()
    drain.assert_not_awaited()


@pytest.mark.asyncio
async def test_nothing_open_means_nothing_is_done(world: Any) -> None:
    db, drain, sweep = world
    _work(db, "completed")
    with _aware_sqlite_datetimes():
        assert await delivery_watchdog.recover_lost_deliveries() is False
    drain.assert_not_awaited()
    sweep.assert_not_awaited()


async def _run_loop(monkeypatch: pytest.MonkeyPatch, results: list[bool], *, arm_after: int | None = None) -> list[float]:
    """Runs the loop with instant sleeps; returns the wait before each check."""
    monkeypatch.setattr(settings, "delivery_watchdog_seconds", 180)
    waits: list[float] = []
    checks = iter(results)
    done = asyncio.Event()
    real_sleep = asyncio.sleep

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        await real_sleep(0)

    async def check() -> bool:
        if len(waits) == arm_after:
            outbox_trigger.arm_watchdog()
        try:
            return next(checks)
        except StopIteration:
            done.set()
            return False

    monkeypatch.setattr(delivery_watchdog.asyncio, "sleep", sleep)
    monkeypatch.setattr(delivery_watchdog, "recover_lost_deliveries", check)
    task = asyncio.create_task(delivery_watchdog.delivery_watchdog_loop())
    for _ in range(50):
        await real_sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert outbox_trigger.watchdog_wake is None
    return waits


@pytest.mark.asyncio
async def test_after_start_up_the_watchdog_checks_once_then_waits_for_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _run_loop(monkeypatch, [False]) == [180]


@pytest.mark.asyncio
async def test_open_work_is_checked_again_less_often_a_few_times_then_waits_for_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _run_loop(monkeypatch, [True] * 10) == [180, 360, 600, 600]


@pytest.mark.asyncio
async def test_activity_during_a_check_arms_another_check(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _run_loop(monkeypatch, [False, False], arm_after=1) == [180, 180]
