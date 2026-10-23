"""A write whose outcome could not be confirmed never blocks for good: it is looked for again a few
times, a person can settle it (look again, it didn't happen, it happened), and while its task is
paused it doesn't hold back other tasks' writes to the same resource."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from audoryn_execution.runtime import managed
from audoryn_execution.runtime.managed import ManagedRuntimeExecutor
from audoryn_shared.api import integration_foundation_routes as routes
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.domain.identity.principals import HumanPrincipal
from audoryn_shared.execution.contracts import ExecutionProviderError
from audoryn_shared.infrastructure.database import models as m
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes
from tests.test_efficiency_queries import ORG
from tests.test_planner_summary_and_workspace_guard import Guard

ISSUE = "github.repository.issues.create"
COMMAND = "github.repository.workspace.command"


class Task(Guard):
    """A runtime task paused at step 2 on a write that may have been dispatched."""

    def __init__(self, status: str = "uncertain_outcome", scope: str = ISSUE) -> None:
        super().__init__()
        self.user = uuid4()
        self.item = self.db.add(m.WorkItem, organization_id=ORG, job_id=uuid4(), job_revision_id=uuid4(), status=status,
                                payload={"requestedBy": str(self.user), "integrationOrigin": {"threadId": "t"},
                                         "runtime": {"currentStep": 2, "queueState": status}})
        self.run = self.db.add(m.Run, organization_id=ORG, work_item_id=self.item.id, status=status, correlation_id="c")
        self.step = self.db.add(m.RunStep, run_id=self.run.id, step_index=2, kind="action", status="running",
                                input={"scope": scope}, output={})
        self.key = f"runtime:{self.run.id}:2"
        self.db.add(m.Action, organization_id=ORG, agent_id=self.agent.id, run_id=self.run.id, status="processing",
                    resource_id=self.resource_id, scope=scope, idempotency_key=self.key,
                    payload={"integrationCertainty": "dispatching"})

    async def settle(self, outcome: str) -> dict[str, Any]:
        principal = HumanPrincipal(user_id=self.user, organization_id=ORG, membership_id=uuid4(), role="owner",
                                   permissions=frozenset({"jobs.run"}))
        with _aware_sqlite_datetimes():
            session = BenchSession(Session(self.db.engine, expire_on_commit=False))
            return await routes.settle_outcome(ORG, self.item.id, routes.OutcomeInput(outcome=outcome),  # type: ignore[arg-type]
                                               principal, cast(Any, session))

    def read(self, model: Any, **where: Any) -> Any:
        with Session(self.db.engine) as session:
            return session.scalar(select(model).filter_by(**where))


@pytest.fixture(autouse=True)
def _foundation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)


# --- The shared write guard --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_paused_uncertain_write_does_not_hold_back_another_tasks_write() -> None:
    task = Task("uncertain_outcome")
    task.action("other", ISSUE, certainty="new")
    assert await task.checkpoint(task.request("other", workspace=False)) is False


@pytest.mark.asyncio
async def test_an_uncertain_write_still_being_checked_holds_back_another_write() -> None:
    task = Task("queued")
    task.action("other", ISSUE, certainty="new")
    with pytest.raises(ExecutionProviderError, match="still in flight"):
        await task.checkpoint(task.request("other", workspace=False))


# --- Looking again automatically ---------------------------------------------------------------


def _paused(scope: str = ISSUE, step: bool = True) -> tuple[ManagedRuntimeExecutor, Any, Any, Any]:
    executor = ManagedRuntimeExecutor.__new__(ManagedRuntimeExecutor)
    executor._session = cast(Any, SimpleNamespace(commit=AsyncMock(), scalar=AsyncMock(return_value="DIVINEJMG/school")))
    item = SimpleNamespace(id=uuid4(), status="running", scheduled_at=None, payload={"runtime": {"currentStep": 2}})
    run = SimpleNamespace(id=uuid4(), status="running")
    spec = {"scope": scope, "resourceId": str(uuid4()), "actionInput": {"title": "Uncertain write test"}}
    return executor, item, run, SimpleNamespace(input=spec) if step else None


async def _wait(executor: Any, item: Any, run: Any, step: Any) -> Any:
    error = SimpleNamespace(code="uncertain_outcome", safe_message="Could not confirm.")
    return await executor._wait_integration(item, run, step, error, 2)


@pytest.mark.asyncio
async def test_an_uncertain_write_is_looked_for_again_then_waits_for_a_person(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(managed, "notify_integration_work", AsyncMock())
    executor, item, run, step = _paused()
    delays = []
    for _ in range(settings.integration_outcome_checks):
        outcome = await _wait(executor, item, run, step)
        assert outcome.state == "continue" and item.status == "queued"
        assert item.payload["runtime"]["queueState"] == "checking_outcome"
        delays.append((item.scheduled_at - datetime.now(UTC)).total_seconds())
    assert delays == sorted(delays) and delays[0] > 0
    outcome = await _wait(executor, item, run, step)
    assert outcome.state == "noop" and item.status == "uncertain_outcome"
    # Twice: once when the looking starts (F47: the chat is told it is still being checked), once
    # when it waits for the person.
    calls = managed.notify_integration_work.await_args_list  # type: ignore[attr-defined]
    assert len(calls) == 2 and "look for it again shortly" in calls[0].kwargs["content"]
    told = calls[1].kwargs
    # F21: the write is named, with where to look and the three answers offered in the chat.
    assert told["content"].startswith("I couldn't confirm whether the issue “Uncertain write test” was created in DIVINEJMG/school.")
    assert "It happened, It didn't happen, or Check again" in told["content"]
    assert told["references"] == {"type": "work_item", "id": str(item.id), "controls": "outcome"}


@pytest.mark.asyncio
async def test_the_count_starts_over_for_a_later_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(managed, "notify_integration_work", AsyncMock())
    executor, item, run, step = _paused()
    item.payload["runtime"]["outcomeChecks"] = {"step": 1, "count": 99}
    assert (await _wait(executor, item, run, step)).state == "continue"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,step", [(COMMAND, True), (ISSUE, False)])
async def test_a_workspace_command_or_an_unknown_step_waits_for_a_person(monkeypatch: pytest.MonkeyPatch, scope: str, step: bool) -> None:
    # Running a workspace command again would repeat it rather than look for it.
    monkeypatch.setattr(managed, "notify_integration_work", AsyncMock())
    executor, item, run, saved = _paused(scope, step)
    assert (await _wait(executor, item, run, saved)).state == "noop"
    assert item.status == "uncertain_outcome"


# --- A person settles it -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_again_runs_the_step_which_only_looks_for_the_write() -> None:
    task = Task()
    await task.settle("check")
    item = task.read(m.WorkItem, id=task.item.id)
    assert item.status == "queued" and item.payload["runtime"]["currentStep"] == 2
    assert task.read(m.Action, idempotency_key=task.key).payload["integrationCertainty"] == "dispatching"
    event = task.read(m.OutboxEvent, topic="run.progress")
    assert event.payload["current_step"] == 2 and event.payload["continuation_phase"] == "execute"


@pytest.mark.asyncio
async def test_it_didnt_happen_runs_the_write_again_and_no_longer_holds_back_others() -> None:
    task = Task()
    await task.settle("not_done")
    action = task.read(m.Action, idempotency_key=task.key)
    assert action.payload["integrationCertainty"] == "not_executed"
    assert action.payload["outcomeSettled"]["outcome"] == "not_done"
    assert task.read(m.WorkItem, id=task.item.id).status == "queued"
    # Its next run sends the write as new instead of looking for it.
    assert await task.checkpoint(task.request(task.key, workspace=False)) is False


@pytest.mark.asyncio
async def test_it_happened_completes_the_step_and_carries_on() -> None:
    task = Task()
    await task.settle("done")
    assert task.read(m.Action, idempotency_key=task.key).status == "executed"
    assert task.read(m.RunStep, id=task.step.id).status == "completed"
    item = task.read(m.WorkItem, id=task.item.id)
    assert item.status == "queued" and item.payload["runtime"]["currentStep"] == 3
    event = task.read(m.OutboxEvent, topic="run.progress")
    assert event.payload["current_step"] == 3 and event.payload["continuation_phase"] == "plan"
    assert task.read(m.AuditEvent, event_type="integration.outcome.settled") is not None


@pytest.mark.asyncio
async def test_a_workspace_command_cannot_be_looked_for_but_can_be_settled() -> None:
    task = Task(scope=COMMAND)
    with pytest.raises(HTTPException) as raised:
        await task.settle("check")
    assert raised.value.status_code == 409
    assert (await task.settle("done"))["status"] == "queued"


@pytest.mark.asyncio
async def test_only_a_paused_task_of_your_own_can_be_settled() -> None:
    running = Task("running")
    with pytest.raises(HTTPException) as raised:
        await running.settle("done")
    assert raised.value.status_code == 409
    other = Task()
    other.user = uuid4()
    with pytest.raises(HTTPException) as raised:
        await other.settle("done")
    assert raised.value.status_code == 404
