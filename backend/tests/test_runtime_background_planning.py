import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import SecretStr
from sqlalchemy.exc import PendingRollbackError

from app.api.internal import runtime
from app.bootstrap.settings import Settings, settings
from app.domain.ai.providers import AIProviderError
from app.infrastructure.ai import provider as ai_provider
from app.infrastructure.ai.telemetry import DurableAIInvocationRecorder
from app.infrastructure.redis.coordination import Lease
from app.runtime.managed import ManagedRuntimeExecutor, RuntimeStepOutcome


def message():
    return runtime.ExecutePayload(organization_id=uuid4(), work_item_id=uuid4())


def test_planner_http_timeout_is_independent_of_conversation_timeout(monkeypatch):
    config = Settings(
        **cast(Any, {"_env_file": None}),
        ai_enabled=True,
        ai_provider_api_key=SecretStr("test-only"),
        ai_timeout_seconds=60,
        runtime_planner_timeout_seconds=1500,
    )
    captured = []

    def provider(**kwargs):
        captured.append(kwargs["timeout_seconds"])
        return SimpleNamespace(name="nvidia_nim")

    monkeypatch.setattr(ai_provider, "NvidiaNimProvider", provider)
    gateway = ai_provider.ai_gateway_from_settings(config, planner=True)
    assert isinstance(cast(Any, gateway)._recorder, DurableAIInvocationRecorder)
    ai_provider.ai_gateway_from_settings(config)
    assert captured == [1500, 60]
    assert config.runtime_delivery_timeout_seconds == 120


@pytest.mark.asyncio
async def test_callback_acknowledges_before_long_planning(monkeypatch):
    payload = message()
    monkeypatch.setattr(settings, "runtime_execution_enabled", True)
    monkeypatch.setattr(runtime, "_verify_qstash", Mock())
    monkeypatch.setattr(
        runtime, "CutoverController", lambda _: SimpleNamespace(require_authoritative=Mock())
    )
    coordinator = SimpleNamespace(
        acquire_lock=AsyncMock(side_effect=[Lease("work", "token"), Lease("slot", "token")]),
        close=AsyncMock(),
    )
    monkeypatch.setattr(runtime.RedisCoordinator, "from_settings", lambda: coordinator)
    entered = asyncio.Event()

    async def long_plan(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_execute_runtime_message", long_plan)
    tasks = BackgroundTasks()
    request = SimpleNamespace(
        body=AsyncMock(return_value=json.dumps(payload.model_dump(mode="json")).encode())
    )
    result = await runtime.execute(cast(Any, request), tasks, "signature")
    assert result["status"] == "accepted"
    assert not entered.is_set()
    await tasks()
    await asyncio.wait_for(entered.wait(), 1)
    assert runtime._runtime_tasks
    await runtime.stop_runtime_tasks()
    await asyncio.sleep(0)
    assert not runtime._runtime_tasks


@pytest.mark.asyncio
async def test_duplicate_callback_does_not_start_a_second_planner(monkeypatch):
    payload = message()
    monkeypatch.setattr(settings, "runtime_execution_enabled", True)
    monkeypatch.setattr(runtime, "_verify_qstash", Mock())
    monkeypatch.setattr(
        runtime, "CutoverController", lambda _: SimpleNamespace(require_authoritative=Mock())
    )
    coordinator = SimpleNamespace(acquire_lock=AsyncMock(return_value=None), close=AsyncMock())
    monkeypatch.setattr(runtime.RedisCoordinator, "from_settings", lambda: coordinator)
    tasks = BackgroundTasks()
    request = SimpleNamespace(
        body=AsyncMock(return_value=json.dumps(payload.model_dump(mode="json")).encode())
    )
    result = await runtime.execute(cast(Any, request), tasks, "signature")
    assert result["status"] == "busy"
    assert not tasks.tasks
    coordinator.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_response_returns_202_while_planning_remains_active(monkeypatch):
    payload = message()
    monkeypatch.setattr(settings, "runtime_execution_enabled", True)
    monkeypatch.setattr(runtime, "_verify_qstash", Mock())
    monkeypatch.setattr(
        runtime, "CutoverController", lambda _: SimpleNamespace(require_authoritative=Mock())
    )
    coordinator = SimpleNamespace(
        acquire_lock=AsyncMock(side_effect=[Lease("work", "token"), Lease("slot", "token")])
    )
    monkeypatch.setattr(runtime.RedisCoordinator, "from_settings", lambda: coordinator)
    entered = asyncio.Event()

    async def long_plan(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_execute_runtime_message", long_plan)
    app = FastAPI()
    app.include_router(runtime.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://local"
    ) as client:
        response = await asyncio.wait_for(
            client.post(
                "/internal/v1/runtime/execute",
                json=payload.model_dump(mode="json"),
                headers={"Upstash-Signature": "test"},
            ),
            1,
        )
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    await asyncio.wait_for(entered.wait(), 1)
    await runtime.stop_runtime_tasks()


@pytest.mark.asyncio
async def test_background_capacity_returns_retryable_delivery_response(monkeypatch):
    payload = message()
    monkeypatch.setattr(settings, "runtime_execution_enabled", True)
    monkeypatch.setattr(settings, "runtime_qstash_parallelism", 2)
    monkeypatch.setattr(runtime, "_verify_qstash", Mock())
    monkeypatch.setattr(
        runtime, "CutoverController", lambda _: SimpleNamespace(require_authoritative=Mock())
    )
    coordinator = SimpleNamespace(
        acquire_lock=AsyncMock(side_effect=[Lease("work", "token"), None, None]),
        release_lock=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(runtime.RedisCoordinator, "from_settings", lambda: coordinator)
    request = SimpleNamespace(
        body=AsyncMock(return_value=json.dumps(payload.model_dump(mode="json")).encode())
    )
    with pytest.raises(HTTPException) as caught:
        await runtime.execute(cast(Any, request), BackgroundTasks(), "signature")
    assert caught.value.status_code == 429
    coordinator.release_lock.assert_awaited_once()
    coordinator.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_count,scheduled", [(0, True), (2, False)])
async def test_timeout_recovers_invalid_transaction_and_preserves_run(
    monkeypatch, retry_count, scheduled
):
    payload = message()
    now = datetime.now(UTC)
    item = SimpleNamespace(
        id=payload.work_item_id,
        organization_id=payload.organization_id,
        status="admitted",
        scheduled_at=now - timedelta(seconds=1),
        payload={
            "runtime": {
                "aiRetryCount": retry_count,
                "plannerPhase": "awaiting_ai",
                "plannerPhaseStartedAt": (now - timedelta(seconds=10)).isoformat(),
                "plannerTiming": {"preparationSeconds": 2},
            }
        },
    )
    saved_run = SimpleNamespace(id=uuid4(), status="running", result_summary=None)

    class Session:
        invalid = False
        rollbacks = 0
        commits = 0
        reads = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scalar(self, statement):
            if self.invalid:
                raise PendingRollbackError("Invalid transaction")
            self.reads += 1
            return saved_run if self.reads == 3 else item

        async def rollback(self):
            self.invalid = False
            self.rollbacks += 1

        async def commit(self):
            assert not self.invalid
            self.commits += 1

    session = Session()

    async def fail(**kwargs):
        session.invalid = True
        raise AIProviderError("provider_unavailable", "Planning deadline", retryable=True)

    monkeypatch.setattr(runtime, "session_factory", lambda: session)
    monkeypatch.setattr(
        runtime, "ManagedRuntimeExecutor", lambda _: SimpleNamespace(execute_step=fail)
    )
    monkeypatch.setattr(runtime, "request_runtime_execution", AsyncMock(return_value="retry"))
    monkeypatch.setattr(runtime, "notify_integration_work", AsyncMock())
    monkeypatch.setattr(settings, "ai_max_retries", 2)
    coordinator = SimpleNamespace(release_lock=AsyncMock(), close=AsyncMock())
    output = await runtime._execute_runtime_message(
        payload, cast(Any, coordinator), Lease("work", "token"), Lease("slot", "token")
    )
    assert session.rollbacks == 1
    assert session.commits == 1
    assert saved_run.status == "waiting_ai"
    assert item.status == ("queued" if scheduled else "waiting_ai")
    assert output["retryScheduled"] == scheduled
    assert item.payload["runtime"]["plannerTiming"]["preparationSeconds"] == 2
    assert item.payload["runtime"]["plannerTiming"]["aiResponseSeconds"] >= 10
    assert coordinator.release_lock.await_count == 2


@pytest.mark.asyncio
async def test_run_checkpoint_commits_before_cancellable_planning(monkeypatch):
    item = SimpleNamespace(id=uuid4(), status="running", payload={"runtime": {}})
    saved_run = SimpleNamespace(id=uuid4(), status="running")
    executor = object.__new__(ManagedRuntimeExecutor)
    session = SimpleNamespace(commit=AsyncMock())
    executor._session = cast(Any, session)
    executor._load_context = AsyncMock(return_value=(object(), object(), object(), object()))
    executor._ensure_run = AsyncMock(return_value=saved_run)
    executor._load_steps = AsyncMock(return_value=[])

    monkeypatch.setattr(settings, "runtime_planner_timeout_seconds", 0.02)

    async def plan(**kwargs):
        session.commit.assert_awaited_once()
        assert item.payload["runtime"]["plannerPhase"] == "preparing_tools"
        await asyncio.Event().wait()

    executor._plan_next_step = cast(Any, plan)
    with pytest.raises(AIProviderError, match="configured time budget"):
        await executor.execute_step(item=cast(Any, item), expected_step=0)
    assert saved_run.id


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_preparation_releases_transaction_and_records_separate_ai_duration(cancelled, monkeypatch):
    monkeypatch.setattr(settings, "smart_planner_enabled", False)
    executor = object.__new__(ManagedRuntimeExecutor)
    item = SimpleNamespace(id=uuid4(), status="running", payload={"runtime": {}})
    saved_run = SimpleNamespace(id=uuid4(), status="running")
    session = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock(), refresh=AsyncMock())
    executor._session = cast(Any, session)

    async def tools(**kwargs):
        await asyncio.sleep(0.005)
        executor._tool_preparation_metrics = {"policyChecks": 3, "policySeconds": 0.003}
        return [{"scope": "github.repository.metadata.read"}]

    executor._planner_tools = cast(Any, tools)
    executor._worker_memory_context = AsyncMock(return_value={})
    executor._http_first_observations = AsyncMock(return_value=[])

    async def choose(**kwargs):
        session.commit.assert_awaited_once()
        assert item.payload["runtime"]["plannerPhase"] == "awaiting_ai"
        await asyncio.sleep(0.005)
        if cancelled:
            item.status, saved_run.status = "cancelled", "cancelled"
        return SimpleNamespace(summary="Inspect")

    executor._choose_planner_decision = cast(Any, choose)
    await executor._plan_next_step(
        item=cast(Any, item),
        run=cast(Any, saved_run),
        job=cast(Any, SimpleNamespace()),
        revision=cast(Any, SimpleNamespace(definition={})),
        worker=cast(Any, SimpleNamespace(profile={})),
        agent=cast(Any, object()),
        steps=[],
    )
    timing = item.payload["runtime"]["plannerTiming"]
    assert timing["preparationSeconds"] > 0
    assert timing["preparationBreakdown"]["policyChecks"] == 3
    assert timing["preparationBreakdown"]["policySeconds"] == 0.003
    assert "memorySeconds" in timing["preparationBreakdown"]
    assert "observationSeconds" in timing["preparationBreakdown"]
    if cancelled:
        session.flush.assert_not_awaited()
        assert saved_run.status == "cancelled"
    else:
        assert timing["aiResponseSeconds"] > 0
        assert item.payload["runtime"]["plannerPhase"] == "planned"


@pytest.mark.asyncio
async def test_invocation_telemetry_uses_separate_committed_sessions(monkeypatch):
    from app.infrastructure.ai import telemetry
    from app.infrastructure.database import session as database

    sessions = []

    class Session:
        committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def commit(self):
            self.committed = True

    def factory():
        row = Session()
        sessions.append(row)
        return row

    identity = uuid4()
    recorder = SimpleNamespace(start=AsyncMock(return_value=identity), finish=AsyncMock())
    monkeypatch.setattr(database, "session_factory", factory)
    monkeypatch.setattr(telemetry, "SQLAlchemyAIInvocationRecorder", lambda _: recorder)
    durable = DurableAIInvocationRecorder()
    assert await durable.start() == identity
    await durable.finish(identity)
    assert len(sessions) == 2
    assert all(row.committed for row in sessions)


@pytest.mark.asyncio
async def test_loss_of_lease_cancels_owner_before_overlap(monkeypatch):
    coordinator = SimpleNamespace(renew_lock=AsyncMock(return_value=False))
    owner = SimpleNamespace(cancel=Mock())
    sleep = AsyncMock()
    monkeypatch.setattr(runtime.asyncio, "sleep", sleep)
    await runtime._renew_runtime_leases(coordinator, (Lease("work", "token"),), owner)
    owner.cancel.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase,state", [("preparing_tools", "waiting_configuration"), ("planned", "uncertain_outcome")]
)
async def test_unexpected_background_error_records_wait_without_blind_replay(
    monkeypatch, phase, state
):
    payload = message()
    item = SimpleNamespace(
        id=payload.work_item_id,
        organization_id=payload.organization_id,
        status="admitted",
        scheduled_at=datetime.now(UTC) - timedelta(seconds=1),
        payload={"runtime": {"plannerPhase": phase}},
    )
    saved_run = SimpleNamespace(status="running")
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[item, item, saved_run]),
        rollback=AsyncMock(),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(runtime, "session_factory", factory)
    monkeypatch.setattr(
        runtime,
        "ManagedRuntimeExecutor",
        lambda _: SimpleNamespace(
            execute_step=AsyncMock(side_effect=ValueError("internal detail"))
        ),
    )
    monkeypatch.setattr(runtime, "notify_integration_work", AsyncMock())
    signal = AsyncMock()
    monkeypatch.setattr(runtime, "request_runtime_execution", signal)
    coordinator = SimpleNamespace(release_lock=AsyncMock(), close=AsyncMock())
    output = await runtime._execute_runtime_message(
        payload, cast(Any, coordinator), Lease("work", "token"), Lease("slot", "token")
    )
    assert output["status"] == state
    assert item.status == saved_run.status == state
    assert "internal detail" not in str(output["summary"])
    session.rollback.assert_awaited_once()
    session.commit.assert_awaited_once()
    signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_continuation_signals_after_release_with_distinct_identity(monkeypatch):
    payload = message()
    item = SimpleNamespace(
        id=payload.work_item_id,
        status="admitted",
        scheduled_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=item))

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(runtime, "session_factory", factory)
    outcome = RuntimeStepOutcome("continue", payload.work_item_id, uuid4(), 0, "Planned", "execute")
    monkeypatch.setattr(
        runtime,
        "ManagedRuntimeExecutor",
        lambda _: SimpleNamespace(execute_step=AsyncMock(return_value=outcome)),
    )
    coordinator = SimpleNamespace(release_lock=AsyncMock(), close=AsyncMock())

    async def signal(**kwargs):
        assert coordinator.release_lock.await_count == 2
        assert kwargs["reason"] == f"continuation-execute:background:{outcome.run_id}"
        return "queued"

    monkeypatch.setattr(runtime, "request_runtime_execution", signal)
    output = await runtime._execute_runtime_message(
        payload, cast(Any, coordinator), Lease("work", "token"), Lease("slot", "token")
    )
    assert output["continuationQueued"]
