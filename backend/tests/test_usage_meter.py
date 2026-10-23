"""Usage meters (improvement plan, phase 0): what each runtime step and chat message costs."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from audoryn_execution.api.internal import runtime
from audoryn_shared.application.services.ai_gateway import observe_inference
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.domain.ai.providers import AIInvocationContext, AIProviderError, AIResponse
from audoryn_shared.infrastructure.database import models as m
from audoryn_shared.infrastructure.database import usage_records
from audoryn_shared.infrastructure.qstash.provider import UpstashQStashProvider
from audoryn_shared.infrastructure.redis.coordination import Lease
from audoryn_shared.observability.usage import (
    MeteredRedis,
    Usage,
    label_usage,
    meter_database,
    metered,
)


def test_database_statements_and_written_rows_count_only_inside_a_meter() -> None:
    engine = create_engine("sqlite://")
    meter_database(engine)
    meter_database(engine)  # idempotent: still one listener
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE t (x INTEGER)"))
        with metered() as usage:
            connection.execute(text("INSERT INTO t VALUES (1), (2), (3)"))
            connection.execute(text("SELECT x FROM t")).all()
        connection.execute(text("SELECT x FROM t")).all()
    assert usage.db_statements == 2
    # SQLite reports rows for writes only; PostgreSQL (asyncpg) also reports rows returned.
    assert usage.db_rows == 3


@pytest.mark.asyncio
async def test_tasks_count_into_the_meter_that_started_them_and_meters_do_not_nest() -> None:
    from audoryn_shared.observability.usage import count_qstash_message

    with metered() as outer:
        count_qstash_message()
        await asyncio.create_task(asyncio.sleep(0, count_qstash_message()))
        with metered() as inner:
            count_qstash_message()
            label_usage(phase="plan")
    count_qstash_message()  # outside any meter: not counted
    assert (outer.qstash_messages, inner.qstash_messages) == (2, 1)
    assert inner.labels == {"phase": "plan"} and outer.labels == {}
    assert outer.seconds >= inner.seconds >= 0


@pytest.mark.asyncio
async def test_each_qstash_publish_counts_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"messageId": "m1"}))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport))
    provider = UpstashQStashProvider(base_url="https://qstash.test", token="t")
    with metered() as usage:
        await provider.publish(destination="https://x.test/run", body="{}", idempotency_key="k")
        await provider.publish(destination="https://x.test/run", body="{}", idempotency_key="k2")
    assert usage.qstash_messages == 2


@pytest.mark.asyncio
async def test_redis_commands_and_pipelines_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    from redis.asyncio import Redis
    from redis.asyncio.client import Pipeline

    monkeypatch.setattr(Redis, "execute_command", AsyncMock(return_value=b"OK"))
    monkeypatch.setattr(Pipeline, "execute", AsyncMock(return_value=[]))
    client = MeteredRedis.from_url("redis://localhost:6379/0")
    with metered() as usage:
        await client.set("a", "1")
        await client.get("a")
        pipeline = client.pipeline()
        pipeline.command_stack.extend([object(), object(), object()])
        await pipeline.execute()
    assert usage.redis_commands == 5


@pytest.mark.asyncio
async def test_ai_calls_count_tokens_and_failures() -> None:
    context = AIInvocationContext(organization_id=uuid4())

    async def answer() -> AIResponse:
        return AIResponse(text="{}", provider="p", model="fast", usage={"prompt_tokens": 120, "completion_tokens": 30})

    async def fail() -> AIResponse:
        raise AIProviderError("rate_limited", "busy", retryable=True)

    with metered() as usage:
        await observe_inference(answer, role="planner", model="fast", provider="p", context=context)
        with pytest.raises(AIProviderError):
            await observe_inference(fail, role="planner", model="strong", provider="p", context=context)
    assert (usage.ai_calls, usage.ai_failed_calls) == (2, 1)
    assert (usage.ai_tokens_in, usage.ai_tokens_out) == (120, 30)
    assert usage.ai_models == {"fast": 1, "strong": 1}


@pytest.mark.asyncio
async def test_usage_is_saved_as_one_row_and_saving_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.runtime_benchmark import BenchSession  # also teaches SQLite the JSONB type

    engine = create_engine("sqlite://", poolclass=StaticPool)
    m.Base.metadata.create_all(engine, tables=[cast(Any, m.UsageRecord.__table__)])
    from audoryn_shared.infrastructure.database import session as session_module

    monkeypatch.setattr(session_module, "session_factory", lambda: BenchSession(Session(engine)))
    monkeypatch.setattr(settings, "usage_records_enabled", True)
    usage = Usage(db_statements=7, qstash_messages=2, ai_calls=1, ai_tokens_in=50, seconds=1.25)
    usage.ai_models["fast"] += 1
    usage.prompt_chars["planner.JOB"] += 40
    organization_id, work_item_id = uuid4(), uuid4()
    await usage_records.record_usage(usage, kind="runtime.step", organization_id=organization_id,
                                     work_item_id=work_item_id, step=3, phase="plan", outcome="continue")
    with Session(engine) as session:
        [row] = session.scalars(select(m.UsageRecord)).all()
    assert (row.kind, row.step, row.phase, row.outcome) == ("runtime.step", 3, "plan", "continue")
    assert (row.db_statements, row.qstash_messages, row.ai_calls, row.ai_tokens_in) == (7, 2, 1, 50)
    assert row.ai_models == {"fast": 1} and row.duration_ms == 1250
    assert row.prompt_chars == {"planner.JOB": 40}

    monkeypatch.setattr(session_module, "session_factory", Mock(side_effect=RuntimeError("database down")))
    await usage_records.record_usage(usage, kind="runtime.step", organization_id=organization_id)
    monkeypatch.setattr(settings, "usage_records_enabled", False)
    await usage_records.record_usage(usage, kind="runtime.step", organization_id=organization_id)
    with Session(engine) as session:
        assert len(session.scalars(select(m.UsageRecord)).all()) == 1


def _accepting_runtime(monkeypatch: pytest.MonkeyPatch, locks: list[Any]) -> SimpleNamespace:
    monkeypatch.setattr(settings, "runtime_execution_enabled", True)
    monkeypatch.setattr(settings, "runtime_qstash_parallelism", 1)
    monkeypatch.setattr(runtime, "_verify_qstash", Mock())
    monkeypatch.setattr(runtime, "CutoverController", lambda _: SimpleNamespace(require_authoritative=Mock()))
    coordinator = SimpleNamespace(acquire_lock=AsyncMock(side_effect=locks), release_lock=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(runtime.RedisCoordinator, "from_settings", lambda: coordinator)
    recorded: list[dict[str, Any]] = []

    async def record(usage: Usage, **fields: Any) -> None:
        recorded.append({**fields, "redis": usage.redis_commands, "statements": usage.db_statements})

    monkeypatch.setattr(runtime, "record_usage", record)
    return SimpleNamespace(coordinator=coordinator, recorded=recorded)


def _request(payload: Any) -> Any:
    return SimpleNamespace(body=AsyncMock(return_value=json.dumps(payload.model_dump(mode="json")).encode()))


@pytest.mark.asyncio
async def test_a_turned_away_delivery_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = runtime.ExecutePayload(organization_id=uuid4(), work_item_id=uuid4(), expected_step=2)
    fake = _accepting_runtime(monkeypatch, [None])
    from fastapi import BackgroundTasks

    result = await runtime.execute(cast(Any, _request(payload)), BackgroundTasks(), "signature")
    assert result["status"] == "busy"
    [record] = fake.recorded
    assert record["kind"] == "runtime.delivery" and record["outcome"] == "busy" and record["step"] == 2
    assert record["work_item_id"] == payload.work_item_id


@pytest.mark.asyncio
async def test_an_accepted_delivery_records_its_step_with_the_executor_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = runtime.ExecutePayload(organization_id=uuid4(), work_item_id=uuid4())
    fake = _accepting_runtime(monkeypatch, [Lease("work", "t"), Lease("slot", "t")])
    run_id = uuid4()

    async def step(message: Any, coordinator: Any, lease: Any, slot: Any) -> dict[str, Any]:
        label_usage(phase="plan", run_id=run_id)
        return {"status": "ok", "state": "continue"}

    monkeypatch.setattr(runtime, "_execute_runtime_message", step)
    from fastapi import BackgroundTasks

    tasks = BackgroundTasks()
    await runtime.execute(cast(Any, _request(payload)), tasks, "signature")
    await tasks()
    await asyncio.gather(*runtime._runtime_tasks)
    [record] = fake.recorded
    assert (record["kind"], record["phase"], record["outcome"], record["run_id"]) == ("runtime.step", "plan", "continue", run_id)


@pytest.mark.asyncio
async def test_usage_report_totals_by_kind_and_work_item(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes
    from scripts.usage_report import report

    engine = create_engine("sqlite://", poolclass=StaticPool)
    m.Base.metadata.create_all(engine, tables=[cast(Any, m.UsageRecord.__table__)])
    from audoryn_shared.infrastructure.database import session as session_module

    monkeypatch.setattr(session_module, "session_factory", lambda: BenchSession(Session(engine)))
    organization_id, item = uuid4(), uuid4()
    with Session(engine) as session:
        for phase, rows in (("plan", 40), ("execute", 10), ("plan", 30)):
            session.add(m.UsageRecord(organization_id=organization_id, work_item_id=item, kind="runtime.step",
                                      phase=phase, db_rows=rows, ai_models={}))
        session.commit()
    with _aware_sqlite_datetimes():
        text_report = await report(hours=1, top=5)
    lines = text_report.splitlines()
    plan = next(line for line in lines if line.startswith("runtime.step") and " plan " in line)
    assert plan.split()[2:5] == ["2", "0", "0"] and plan.split()[5] == "70"
    assert any(line.startswith(str(item)) and line.split()[4] == "80" for line in lines)
