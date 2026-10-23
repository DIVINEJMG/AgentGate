"""Improvement plan, phase 1: the runtime engine does the same work with fewer wake-ups, reads and
queries. Each test checks the new behaviour against the previous one."""
from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from audoryn_execution.api.internal import runtime
from audoryn_execution.runtime.admission import admission_order, next_admission
from audoryn_execution.runtime.managed import ManagedRuntimeExecutor, RuntimeStepOutcome
from audoryn_shared.infrastructure.database import models as m
from audoryn_shared.runtime import outbox_publisher
from audoryn_shared.runtime.qstash_trigger import RuntimeSignalResult
from tests.test_efficiency_queries import Database

# ---------- admission: one row, same choice ----------


def _queue(db: Database, rng: random.Random, now: datetime) -> list[Any]:
    organizations = [uuid4() for _ in range(4)]
    agent = db.add(m.AgentIdentity, organization_id=organizations[0], name="a")
    worker = db.add(m.Worker, organization_id=organizations[0], agent_identity_id=agent.id)
    job = db.add(m.Job, organization_id=organizations[0], worker_id=worker.id)
    revision = db.add(m.JobRevision, job_id=job.id, definition={})
    items = []
    for _ in range(rng.randint(1, 25)):
        status = rng.choice(["queued"] * 4 + ["admitted", "running", "completed"])
        created = now - timedelta(seconds=rng.choice([0, 1, 299, 300, 301, 899, 900, 3_599, 3_600, 3_601, 9_000]))
        items.append(db.add(
            m.WorkItem, organization_id=rng.choice(organizations), job_id=job.id, job_revision_id=revision.id,
            status=status, priority=rng.choice(["low", "normal", "high", "urgent", "unknown"]),
            created_at=created, scheduled_at=now - timedelta(seconds=rng.choice([0, 5])) if rng.random() < 0.9 else now + timedelta(minutes=1),
            payload={"bulk": "x" * 1_000}))
    return items


@pytest.mark.parametrize("seed", range(40))
def test_admission_picks_the_same_item_as_before_reading_one_row(seed: int) -> None:
    rng = random.Random(seed)
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    db = Database([m.AgentIdentity, m.Worker, m.Job, m.JobRevision, m.WorkItem])
    items = _queue(db, rng, now)
    queued = [i for i in items if i.status == "queued" and i.scheduled_at.replace(tzinfo=UTC) <= now]
    active = Counter(i.organization_id for i in items if i.status in {"admitted", "running"})
    expected = admission_order(queued, now=now, active_by_organization=active)
    chosen = db.session.scalar(next_admission(now))
    assert chosen == (expected[0].id if expected else None)


def test_admission_query_reads_only_an_id_and_compiles_for_postgresql() -> None:
    sql = str(next_admission(datetime.now(UTC)).compile(dialect=cast(Any, postgresql.dialect())))
    assert sql.startswith("SELECT work_items.id \nFROM work_items")
    assert "payload" not in sql and "LIMIT" in sql


def test_waiting_for_admission_backs_off_to_thirty_seconds() -> None:
    assert [runtime.admission_wait_seconds(waits) for waits in range(9)] == [5, 5, 10, 10, 20, 20, 30, 30, 30]


# ---------- planner evidence: same observations, earlier outputs read once ----------


def _step(index: int, status: str, output: dict[str, Any], scope: str = "bench.record.read") -> m.RunStep:
    return m.RunStep(id=uuid4(), run_id=uuid4(), step_index=index, kind="action", status=status,
                     input={"scope": scope, "title": f"t{index}", "resourceId": "r", "actionInput": {"key": index}},
                     output=output, updated_at=datetime(2026, 10, 13, tzinfo=UTC))


def _steps() -> list[m.RunStep]:
    browser = {"summary": "page", "data": {"output": {"observation": {
        "id": "o1", "sessionId": "s1", "url": "https://example.com", "visibleText": "x" * 20_000,
        "elements": [{"ref": "e1", "tag": "input"}, {"ref": "e2", "tag": "a", "text": "y" * 500}]}}}}
    return [
        _step(0, "completed", {"summary": "read", "data": {"output": {"text": "z" * 30_000}, "verification": {"verified": True, "summary": "ok"}}}),
        _step(1, "completed", browser, scope="browser.page.read"),
        _step(2, "failed", {"summary": "boom", "data": {"integrationFailure": {"code": "x", "executed": False}}}),
        _step(3, "failed", {"summary": "not evidence"}),
        _step(4, "pending", {}),
    ]


@pytest.mark.asyncio
async def test_cached_observations_equal_the_previous_observations() -> None:
    executor = cast(Any, object.__new__(ManagedRuntimeExecutor))
    steps = _steps()
    expected = ManagedRuntimeExecutor._planner_observations(executor, steps)
    first = await ManagedRuntimeExecutor._cached_observations(executor, steps)
    again = await ManagedRuntimeExecutor._cached_observations(executor, steps)
    assert first == expected and again == expected
    # Callers may change what they receive without changing the cache.
    first[0]["summary"] = "changed"
    assert await ManagedRuntimeExecutor._cached_observations(executor, steps) == expected
    # A changed step (new updated_at) is read again.
    steps[0].output = {"summary": "rewritten"}
    steps[0].updated_at = datetime(2026, 10, 14, tzinfo=UTC)
    assert (await ManagedRuntimeExecutor._cached_observations(executor, steps))[0]["summary"] == "rewritten"


# ---------- one wake-up per step ----------


def _event(payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), topic="run.progress", aggregate_id=str(uuid4()), payload=payload)


@pytest.mark.asyncio
async def test_runtime_owned_progress_updates_the_workspace_without_a_second_wake_up(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_runtime_continuation_outbox import _Outbox, _Session

    organization_id, work_item_id = uuid4(), uuid4()
    base = {"organization_id": str(organization_id), "work_item_id": str(work_item_id), "current_step": 2,
            "continuation_phase": "plan"}
    runtime_owned, external = _event({**base, "wakeup": "runtime"}), _event(base)
    session = _Session()
    outbox = _Outbox(session, runtime_owned, external)
    dispatched: list[dict[str, Any]] = []
    realtime: list[str] = []

    async def publish(**kwargs: Any) -> RuntimeSignalResult:
        dispatched.append(kwargs)
        return RuntimeSignalResult(message_id="m", state="queued", detail=None, updated_at="")

    async def emit(bus: Any, topic: str, payload: dict[str, Any], **_: Any) -> None:
        realtime.append(topic)

    bus = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(outbox_publisher.RedisRealtimeBus, "from_settings", classmethod(lambda cls: bus))

    monkeypatch.setattr(outbox_publisher, "session_factory", lambda: session)
    monkeypatch.setattr(outbox_publisher, "TransactionalOutbox", lambda s: outbox)
    monkeypatch.setattr(outbox_publisher, "request_runtime_execution_detailed", publish)
    monkeypatch.setattr(outbox_publisher, "_publish_realtime", emit)
    monkeypatch.setattr(outbox_publisher, "web_research_destination", lambda: None)
    assert await outbox_publisher.drain_outbox_batch() == 2
    assert len(dispatched) == 1 and realtime == ["run.progress", "run.progress"]
    bus.close.assert_awaited_once()  # one realtime connection for the whole drain


async def _run_continuation(monkeypatch: pytest.MonkeyPatch, *, sent: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    organization_id, work_item_id, run_id = uuid4(), uuid4(), uuid4()
    message = runtime.ExecutePayload(organization_id=organization_id, work_item_id=work_item_id, expected_step=1)
    outcome = RuntimeStepOutcome("continue", work_item_id, run_id, 1, "planned", "execute", "bench:r1")
    item = SimpleNamespace(id=work_item_id, organization_id=organization_id, status="running",
                           scheduled_at=datetime.now(UTC) - timedelta(seconds=1), payload={})

    class _ItemSession:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def scalar(self, *_: Any) -> Any:
            return item

    class _Executor:
        def __init__(self, session: Any) -> None:
            pass

        async def execute_step(self, **_: Any) -> RuntimeStepOutcome:
            return outcome

    signals: list[dict[str, Any]] = []
    saved: list[dict[str, Any]] = []

    async def request(**kwargs: Any) -> str | None:
        signals.append(kwargs)
        return "message" if sent else None

    async def durable(message: Any, outcome: RuntimeStepOutcome) -> None:
        saved.append({"step": outcome.current_step, "phase": outcome.continuation_phase, "partition": outcome.queue_partition})

    from unittest.mock import AsyncMock

    coordinator = SimpleNamespace(renew_lock=AsyncMock(return_value=True), release_lock=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(runtime, "session_factory", lambda: _ItemSession())
    monkeypatch.setattr(runtime, "ManagedRuntimeExecutor", _Executor)
    monkeypatch.setattr(runtime, "request_runtime_execution", request)
    monkeypatch.setattr(runtime, "_durable_continuation", durable)
    await runtime._execute_runtime_message(message, cast(Any, coordinator), cast(Any, "lease"), cast(Any, "slot"))
    return signals, saved


@pytest.mark.asyncio
async def test_each_step_sends_one_wake_up_in_its_resource_partition(monkeypatch: pytest.MonkeyPatch) -> None:
    signals, saved = await _run_continuation(monkeypatch, sent=True)
    [signal] = signals
    assert signal["expected_step"] == 1 and signal["queue_partition"] == "bench:r1"
    assert signal["reason"].startswith("continuation-execute:")
    assert saved == []


@pytest.mark.asyncio
async def test_a_wake_up_that_cannot_be_sent_is_saved_for_the_outbox(monkeypatch: pytest.MonkeyPatch) -> None:
    _, saved = await _run_continuation(monkeypatch, sent=False)
    assert saved == [{"step": 1, "phase": "execute", "partition": "bench:r1"}]


# ---------- duplicates are recognised before anything is loaded ----------


@pytest.mark.asyncio
async def test_a_stale_delivery_is_ignored_without_any_query() -> None:
    class _NoQueries:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"session.{name} used by a stale delivery")

    executor = cast(Any, object.__new__(ManagedRuntimeExecutor))
    executor._session = _NoQueries()
    item = SimpleNamespace(id=uuid4(), status="running", payload={"runtime": {"currentStep": 3}})
    outcome = await ManagedRuntimeExecutor.execute_step(executor, item=cast(Any, item), expected_step=2)
    assert (outcome.state, outcome.current_step) == ("noop", 3)


def test_run_steps_planning_reads_without_outputs_raise_if_an_output_is_used() -> None:
    db = Database([m.RunStep])
    run_id = uuid4()
    db.add(m.RunStep, run_id=run_id, step_index=0, kind="action", status="completed", input={}, output={"big": "x"})
    from sqlalchemy.orm import Session, defer

    with Session(db.engine) as session:
        [step] = session.scalars(select(m.RunStep).options(defer(m.RunStep.output, raiseload=True))).all()
        with pytest.raises(Exception, match="raiseload"):
            _ = step.output
        session.scalars(select(m.RunStep).where(m.RunStep.id == step.id).execution_options(populate_existing=True)).all()
        assert step.output == {"big": "x"}


# ---------- slow planning is allowed; a silent stream is a failed connection ----------


def test_a_streamed_response_has_the_whole_budget_but_a_silence_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from audoryn_shared.bootstrap.settings import settings
    from audoryn_shared.infrastructure.ai.streaming import transport_timeout

    monkeypatch.setattr(settings, "planner_stream_silence_seconds", 300)
    streamed = transport_timeout(2000, stream=True)
    assert (streamed.connect, streamed.read, streamed.write) == (2000, 300, 2000)
    assert transport_timeout(2000, stream=False) == httpx.Timeout(2000)
    # Short interactive budgets are unchanged.
    assert transport_timeout(60, stream=True) == httpx.Timeout(60)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["nvidia", "openrouter"])
@pytest.mark.parametrize(("error", "stream", "category"), [
    ("read", True, "provider_unavailable"),   # silent stream: a failed connection
    ("read", False, "timeout"),               # not streamed: unchanged
    ("connect", True, "timeout"),             # not a silence: unchanged
])
async def test_only_a_silent_stream_becomes_a_failed_connection(
    monkeypatch: pytest.MonkeyPatch, adapter: str, error: str, stream: bool, category: str,
) -> None:
    import httpx

    from audoryn_shared.bootstrap.settings import settings
    from audoryn_shared.domain.ai.providers import AIProviderError, AITextRequest
    from audoryn_shared.infrastructure.ai.model_profiles import PlannerModelProfile
    from audoryn_shared.infrastructure.ai.nvidia import NvidiaNimProvider
    from audoryn_shared.infrastructure.ai.openrouter import OpenRouterPlannerProvider

    monkeypatch.setattr(settings, "planner_stream_silence_seconds", 300)
    timeouts: list[Any] = []
    real_init = httpx.AsyncClient.__init__

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        timeouts.append(kwargs.get("timeout"))
        real_init(self, *args, **kwargs)

    def fail(request: httpx.Request) -> httpx.Response:
        raise (httpx.ReadTimeout if error == "read" else httpx.ConnectTimeout)("no bytes", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
    transport = httpx.MockTransport(fail)
    profile = PlannerModelProfile(context_tokens=100000)
    provider: Any
    if adapter == "nvidia":
        provider = NvidiaNimProvider(coordinator_api_key="test", planner_profile=profile, transport=transport,
                                     timeout_seconds=2000)
    else:
        provider = OpenRouterPlannerProvider(api_key="test", allowed_providers=["host"], data_allowed=True,
                                             transport=transport, profiles={"m:free": profile}, timeout_seconds=2000)
    with pytest.raises(AIProviderError) as caught:
        await provider.generate_text(model="m:free" if adapter == "openrouter" else "m",
                                     request=AITextRequest("", "", stream=stream))
    assert caught.value.category == category and caught.value.retryable
    assert timeouts[-1].read == (300 if stream else 2000)


# ---------- the tool list: same catalog, fewer reads ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("connections", [1, 6])
async def test_catalog_checks_credentials_in_one_query_and_the_snapshot_reads_once(
    monkeypatch: pytest.MonkeyPatch, connections: int,
) -> None:
    from dataclasses import replace

    from audoryn_shared.api import integration_capability_routes as routes
    from audoryn_shared.execution.providers.registry import ProviderRegistry
    from scripts.runtime_benchmark import BenchProvider
    from tests.test_efficiency_queries import AsyncAdapter

    provider = BenchProvider()
    read, write = provider.manifest.capabilities
    # Writing needs a stored credential, so only connections with one offer it.
    provider.manifest = replace(provider.manifest, capabilities=(read, replace(write, requires_credential=True)))
    monkeypatch.setattr(routes, "execution_provider_registry", lambda: ProviderRegistry((provider,)))
    db = Database([m.Integration, m.IntegrationCredential])
    organization_id = uuid4()
    credentialed = set()
    for index in range(connections):
        integration = db.add(m.Integration, organization_id=organization_id, provider="bench",
                             display_name=f"store {index}", status="connected", config={})
        if index % 2 == 0:
            db.add(m.IntegrationCredential, integration_id=integration.id, ciphertext="x")
            credentialed.add(str(integration.id))
    session = cast(Any, AsyncAdapter(db.session))
    db.statements.clear()
    catalog = await routes._catalog(session, organization_id)
    assert len(db.statements) == 2
    for resource in catalog["resources"]:
        expected = {"bench.record.read", "bench.record.write"} if resource["id"] in credentialed else {"bench.record.read"}
        assert set(resource["scopes"]) == expected

    db.statements.clear()
    with routes.catalog_snapshot():
        first = await routes._catalog(session, organization_id)
        first["resources"].clear()  # a caller changing its copy does not change the snapshot
        second = await routes._catalog(session, organization_id)
    assert second == catalog and len(db.statements) == 2
    await routes._catalog(session, organization_id)  # outside the snapshot: read again
    assert len(db.statements) == 4
