"""F11: a due runtime step runs in the serving process; QStash keeps delays, full capacity and
recovery."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.internal import runtime as runtime_routes
from app.runtime import outbox_trigger, qstash_trigger


@pytest.fixture
def dispatch(monkeypatch: pytest.MonkeyPatch):
    """Publishes and in-process starts, recorded; the backend is serving."""
    calls = SimpleNamespace(published=[], here=[], here_result="started")

    class _Provider:
        async def publish(self, **kwargs):
            calls.published.append(kwargs)
            return SimpleNamespace(id="qstash-message")

    async def run_here(organization_id, work_item_id, step, reason):
        calls.here.append((work_item_id, step, reason))
        return calls.here_result

    async def nothing() -> None:
        return None

    monkeypatch.setattr(qstash_trigger.settings, "runtime_execution_enabled", True)
    monkeypatch.setattr(qstash_trigger.settings, "qstash_runtime_execute_url", "https://agentgate.test/internal/v1/runtime/execute")
    monkeypatch.setattr(qstash_trigger.settings, "runtime_run_local", True)
    monkeypatch.setattr(qstash_trigger.UpstashQStashProvider, "from_settings", classmethod(lambda cls: _Provider()))
    monkeypatch.setattr(qstash_trigger, "_clear_dispatch_health", nothing)
    monkeypatch.setattr(runtime_routes, "run_here", run_here)
    monkeypatch.setattr(outbox_trigger, "serving", True)
    return calls


async def _signal(**options):
    return await qstash_trigger.request_runtime_execution_detailed(
        organization_id=uuid4(), work_item_id=uuid4(), expected_step=4, reason="continuation-plan", **options)


@pytest.mark.asyncio
async def test_due_step_runs_here_without_a_qstash_message(dispatch) -> None:
    result = await _signal()
    assert result.message_id == "local:started" and result.state == "queued"
    assert dispatch.here[0][1:] == (4, "continuation-plan") and dispatch.published == []


@pytest.mark.asyncio
async def test_step_already_running_needs_no_wake_up(dispatch) -> None:
    dispatch.here_result = "busy"
    assert (await _signal()).message_id == "local:busy" and dispatch.published == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["delayed", "no_slot", "not_serving", "disabled"])
async def test_qstash_delivers_what_cannot_run_here_now(dispatch, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    options = {"delay_seconds": 30} if case == "delayed" else {}
    if case == "no_slot":
        dispatch.here_result = None
    if case == "not_serving":
        monkeypatch.setattr(outbox_trigger, "serving", False)
    if case == "disabled":
        monkeypatch.setattr(qstash_trigger.settings, "runtime_run_local", False)
    result = await _signal(**options)
    assert result.message_id == "qstash-message" and len(dispatch.published) == 1
    assert dispatch.here == ([(dispatch.here[0][0], 4, "continuation-plan")] if case == "no_slot" else [])


class _Coordinator:
    def __init__(self, held: set[str]) -> None:
        self.held, self.released, self.closed = set(held), [], False

    async def acquire_lock(self, key: str, *, ttl_seconds: int):
        del ttl_seconds
        if key in self.held:
            return None
        self.held.add(key)
        return SimpleNamespace(key=key)

    async def release_lock(self, lease) -> None:
        self.held.discard(lease.key)
        self.released.append(lease.key)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("held, expected", [("step", "busy"), ("slots", None), ("", "started")])
async def test_run_here_claims_the_step_and_a_slot(monkeypatch: pytest.MonkeyPatch, held: str, expected) -> None:
    work_item_id = uuid4()
    monkeypatch.setattr(runtime_routes.settings, "runtime_qstash_parallelism", 2)
    taken = {"step": {f"runtime:{work_item_id}:4"},
             "slots": {f"runtime-slot:{runtime_routes.settings.environment}:{index}" for index in range(2)}}.get(held, set())
    coordinator = _Coordinator(taken)
    started = []

    async def start(message, coordinator_, lease, slot, usage=None):
        started.append((message.expected_step, message.reason, lease.key, slot.key))

    monkeypatch.setattr(runtime_routes.RedisCoordinator, "from_settings", classmethod(lambda cls: coordinator))
    monkeypatch.setattr(runtime_routes, "_start_runtime_task", start)
    monkeypatch.setattr(runtime_routes.CutoverController, "require_authoritative", lambda self, part: None)

    assert await runtime_routes.run_here(uuid4(), work_item_id, 4, "continuation-plan") == expected
    if expected == "started":
        assert started and started[0][:3] == (4, "continuation-plan", f"runtime:{work_item_id}:4")
        assert not coordinator.closed  # the step's task owns it now
    else:
        assert started == [] and coordinator.closed
    if held == "slots":
        assert coordinator.released == [f"runtime:{work_item_id}:4"]  # the step's lease is given back
