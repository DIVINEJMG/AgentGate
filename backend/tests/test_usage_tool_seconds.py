"""Browser and coding time on usage records, and the cost report (commercial plan, phase 1)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from test_f30_0_10 import FakeBrowserRuntime, request_for

from app.bootstrap.settings import settings
from app.execution.coding import service as coding
from app.execution.providers.browser import PlaywrightBrowserProvider
from app.infrastructure.database import models as m
from app.infrastructure.database import usage_records
from app.observability.usage import Usage, browser_time, count_tool_seconds, metered


@pytest.mark.asyncio
async def test_tool_seconds_count_only_inside_a_meter_and_merge() -> None:
    count_tool_seconds(browser=5, coding=5)  # outside any meter: not counted
    with metered() as usage:
        count_tool_seconds(browser=1.5)
        count_tool_seconds(coding=30, browser=-2)  # a negative time is never subtracted
        async with browser_time():
            await asyncio.sleep(0.01)
    assert usage.coding_seconds == 30 and 1.51 <= usage.browser_seconds < 1.6
    total = Usage(browser_seconds=1, coding_seconds=2)
    total.merge(usage)
    assert total.coding_seconds == 32
    assert usage.as_dict()["codingSeconds"] == 30


@pytest.mark.asyncio
async def test_a_browser_action_counts_its_time_even_when_it_fails() -> None:
    provider = PlaywrightBrowserProvider(FakeBrowserRuntime())
    request = request_for(provider, operation="element.click",
                          payload={"sessionId": str(uuid4()),
                                   "locator": {"strategy": "observation_ref", "value": "e3"}})

    async def slow_action(**_: Any) -> Any:
        await asyncio.sleep(0.05)
        raise RuntimeError("site down")

    cast(Any, provider)._execute_authorized = slow_action
    with metered() as usage, pytest.raises(Exception):  # noqa: B017 - any failure still counts its time
        await provider.execute(request=request, configuration={}, credential=None)
    assert usage.browser_seconds >= 0.05 and usage.coding_seconds == 0


def test_coding_time_added_to_a_session_anywhere_is_metered() -> None:
    row = m.CodingSession(active_seconds=100)
    with metered() as usage:
        row.active_seconds += 40
        row.active_seconds = row.active_seconds  # re-saving the same total adds nothing
    row.active_seconds += 5  # outside a meter: not counted
    assert row.active_seconds == 145 and usage.coding_seconds == 40


@pytest.mark.asyncio
async def test_the_idle_sweep_records_its_work_on_its_own_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "coding_execution_enabled", True)
    monkeypatch.setattr(settings, "coding_idle_seconds", 600)
    now = datetime.now(UTC)

    def session_row(status: str, active_since: datetime | None) -> m.CodingSession:
        return m.CodingSession(
            id=uuid4(), organization_id=uuid4(), work_item_id=uuid4(), resource_id=uuid4(), sandbox_id="sandbox",
            status=status, last_activity_at=now - timedelta(hours=1), active_since=active_since, active_seconds=10,
            expires_at=now + timedelta(hours=1), evidence={})

    running, paused = session_row("running", now - timedelta(minutes=2)), session_row("paused", None)
    session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [running, paused])),
                              refresh=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    context = AsyncMock()
    context.__aenter__.return_value = session
    coordinator = SimpleNamespace(acquire_lock=AsyncMock(return_value="lease"), release_lock=AsyncMock(),
                                  close=AsyncMock())
    record = AsyncMock()
    monkeypatch.setattr(coding, "session_factory", lambda: context)
    monkeypatch.setattr(coding.RedisCoordinator, "from_settings", lambda: coordinator)
    monkeypatch.setattr(coding, "record_usage", record)
    runtime = SimpleNamespace(connect=AsyncMock(), pause=AsyncMock(), delete=AsyncMock())
    with metered() as sweep:
        await coding.maintain_coding_sessions(runtime)
    assert running.status == "paused" and 120 <= running.active_seconds - 10 <= 121
    [call] = record.await_args_list  # the paused session was not touched
    assert call.kwargs["kind"] == "coding.idle"
    assert call.kwargs["organization_id"] == running.organization_id
    assert call.kwargs["work_item_id"] == running.work_item_id
    assert call.args[0].coding_seconds == running.active_seconds - 10
    assert sweep.coding_seconds == 0  # not counted twice in the sweep's own meter


def _database(monkeypatch: pytest.MonkeyPatch, *tables: Any) -> Any:
    from scripts.runtime_benchmark import BenchSession  # also teaches SQLite the JSONB type

    engine = create_engine("sqlite://", poolclass=StaticPool)
    m.Base.metadata.create_all(engine, tables=[cast(Any, table.__table__) for table in tables])
    from app.infrastructure.database import session as session_module

    monkeypatch.setattr(session_module, "session_factory", lambda: BenchSession(Session(engine)))
    return engine


@pytest.mark.asyncio
async def test_tool_seconds_are_saved_on_the_usage_record(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _database(monkeypatch, m.UsageRecord)
    monkeypatch.setattr(settings, "usage_records_enabled", True)
    await usage_records.record_usage(Usage(browser_seconds=2.3456, coding_seconds=60), kind="runtime.step",
                                     organization_id=uuid4())
    with Session(engine) as session:
        [row] = session.scalars(select(m.UsageRecord)).all()
    assert (row.browser_seconds, row.coding_seconds) == (2.346, 60)


@pytest.mark.asyncio
async def test_cost_report_prices_usage_only_from_the_given_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.cost_report import collect, render
    from scripts.runtime_benchmark import _aware_sqlite_datetimes

    engine = _database(monkeypatch, m.UsageRecord, m.AIInvocation, m.Artifact)
    busy, quiet, item = uuid4(), uuid4(), uuid4()
    until = datetime.now(UTC) + timedelta(minutes=1)
    since = until - timedelta(days=30)
    with Session(engine) as session:
        session.add_all([
            m.UsageRecord(organization_id=busy, work_item_id=item, kind="runtime.step", ai_tokens_in=900,
                          ai_tokens_out=100, browser_seconds=1800, coding_seconds=3600, ai_models={}),
            m.UsageRecord(organization_id=busy, work_item_id=item, kind="runtime.step", ai_models={}),
            m.UsageRecord(organization_id=quiet, kind="conversation.message", ai_tokens_in=10, ai_models={}),
            m.UsageRecord(organization_id=quiet, kind="conversation.message", ai_models={},
                          created_at=since - timedelta(days=1)),  # before the period
            m.AIInvocation(organization_id=busy, role="planner", provider="p", model="big", success=True,
                           usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000}),
            m.AIInvocation(organization_id=busy, role="fast", provider="p", model="new", success=True,
                           usage={"prompt_tokens": 7}),
            m.Artifact(organization_id=busy, storage_key="k", media_type="text/plain", size_bytes=1024**3,
                       metadata_json={}),
        ])
        session.commit()
    with _aware_sqlite_datetimes():
        usage = await collect(since, until)
    assert (usage[str(busy)].tasks, usage[str(quiet)].messages) == (1, 1)
    assert usage[str(busy)].models == {"big": [1_000_000, 500_000], "new": [7, 0]}
    assert usage[str(busy)].storage_bytes == 1024**3

    quantities = render(usage, since, until, None)
    assert "cost" not in quantities and "No price" not in quantities
    prices = {"models": {"big": {"input": 1.0, "output": 4.0}}, "browserHour": 2, "codingHour": 0.5,
              "storageGbMonth": 3, "currency": "USD"}
    priced = render(usage, since, until, prices).splitlines()
    busy_line = next(line for line in priced if line.startswith(str(busy)))
    # 1 + 2 (tokens) + 1 (half an hour of browser) + 0.5 (an hour of coding) + 3 (1 GB for 30 days)
    assert busy_line.split()[-1] == "7.5000"
    assert priced[-1] == "No price given (not in the cost): new"
