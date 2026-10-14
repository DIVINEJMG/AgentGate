"""A planner action without a summary is not sent back (its title says what it does), and workspace
actions neither wait for nor block writes to the shared resource, nor reconcile when repeated."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.execution.contracts import ExecutionProviderError
from app.execution.provider_executor import DatabaseProviderContextLoader
from app.execution.providers.registry import ProviderRegistry
from app.infrastructure.database import models as m
from app.runtime.planner.adaptive import AdaptiveRuntimePlanner
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes
from tests.test_approvals_phase4 import TABLES
from tests.test_efficiency_queries import ORG, Database
from tests.test_integration_foundation import execution
from tests.test_run_plans import TOOLS

# --- Planner -----------------------------------------------------------------------------------


def _validate(raw: dict[str, Any]) -> Any:
    planner = AdaptiveRuntimePlanner(object())  # type: ignore[arg-type]
    return planner._validate(raw, tools=TOOLS, observations=[{"step": 1}], latest_browser=None)


def test_an_action_without_a_summary_uses_its_title() -> None:
    action = {"decision": "act", "title": "Read a", "instruction": "Read record a.",
              "resourceId": TOOLS[0]["resourceId"], "scope": TOOLS[0]["scope"], "input": {"key": "a"}}
    assert _validate(action).summary == "Read a"
    assert _validate({**action, "summary": "Reading a"}).summary == "Reading a"


def test_a_finish_still_needs_its_summary() -> None:
    with pytest.raises(RuntimeError, match="missing a summary"):
        _validate({"decision": "finish", "title": "Done", "instruction": "All done."})


# --- Workspace actions and the shared write guard ---------------------------------------------


class Guard:
    def __init__(self) -> None:
        self.db = Database(TABLES)
        self.human = self.db.add(m.HumanIdentity, display_name="Owner")
        self.agent = self.db.add(m.AgentIdentity, organization_id=ORG, name="Ada", status="active", created_by=self.human.id)
        self.resource_id = str(uuid4())

    def action(self, key: str, scope: str, certainty: str = "dispatching") -> None:
        self.db.add(m.Action, organization_id=ORG, agent_id=self.agent.id, status="processing", resource_id=self.resource_id,
                    scope=scope, idempotency_key=key, payload={"integrationCertainty": certainty})

    def request(self, key: str, *, workspace: bool) -> Any:
        request, _ = execution()
        capability = replace(request.capability, target="workspace" if workspace else "repository")
        resource = replace(request.resource, id=self.resource_id)
        return replace(request, organization_id=ORG, capability=capability, resource=resource, idempotency_key=key)

    async def checkpoint(self, request: Any) -> bool:
        with _aware_sqlite_datetimes():
            session = BenchSession(Session(self.db.engine, expire_on_commit=False))
            # A provider with no dispatch-evidence hook: only the guard is under test.
            registry = cast(ProviderRegistry, SimpleNamespace(get=lambda name: object()))
            return await DatabaseProviderContextLoader(session, registry).checkpoint_write_dispatch(request)


@pytest.mark.asyncio
async def test_a_stuck_workspace_edit_does_not_block_other_tasks_writes() -> None:
    guard = Guard()
    guard.action("old-edit", "github.repository.workspace.edit")
    guard.action("new-issue", "github.repository.issues.create", certainty="new")
    assert await guard.checkpoint(guard.request("new-issue", workspace=False)) is False


@pytest.mark.asyncio
async def test_a_workspace_edit_does_not_wait_for_another_tasks_uncertain_write() -> None:
    guard = Guard()
    guard.action("old-issue", "github.repository.issues.create")
    guard.action("new-edit", "github.repository.workspace.edit", certainty="new")
    assert await guard.checkpoint(guard.request("new-edit", workspace=True)) is False


@pytest.mark.asyncio
async def test_an_uncertain_outside_write_still_holds_back_other_outside_writes() -> None:
    guard = Guard()
    guard.action("old-issue", "github.repository.issues.create")
    guard.action("new-issue", "github.repository.issues.create", certainty="new")
    with pytest.raises(ExecutionProviderError, match="still in flight"):
        await guard.checkpoint(guard.request("new-issue", workspace=False))


@pytest.mark.asyncio
async def test_a_repeated_workspace_edit_runs_again_but_a_repeated_outside_write_reconciles() -> None:
    guard = Guard()
    guard.action("edit", "github.repository.workspace.edit")
    assert await guard.checkpoint(guard.request("edit", workspace=True)) is False
    guard.action("issue", "github.repository.issues.create")
    assert await guard.checkpoint(guard.request("issue", workspace=False)) is True
