from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from scripts.github_inspection_recovery import resume_inspection, validate_recovery
from scripts.github_scripted_planner import FixtureBlocked, ScriptedPlanner
from tests.test_github_scripted_workflow import MANIFEST


def checkpoint(operation="repository.workspace.diff"):
    resource = str(uuid4())
    item = SimpleNamespace(id=uuid4(), organization_id=uuid4(), status="uncertain_outcome",
        payload={"runtime": {"currentStep": 1, "waitingReason": "old inspection error"}})
    run = SimpleNamespace(id=uuid4(), status="uncertain_outcome")
    completed = SimpleNamespace(kind="action", status="completed", input={"title": "[script:metadata] read"})
    pending = SimpleNamespace(kind="action", status="pending", input={"provider": "github",
        "operation": operation, "scope": "github." + operation, "resourceId": resource,
        "actionInput": {}, "title": "[script:diff] inspect"})
    return item, run, [completed, pending], resource


@pytest.mark.parametrize("operation", ["repository.workspace.edit", "repository.workspace.command.start", "repository.commit.create", "repository.workspace.open"])
def test_uncertain_mutations_cannot_use_inspection_recovery(operation):
    item, run, steps, resource = checkpoint(operation)
    with pytest.raises(FixtureBlocked, match="eligible inspection"):
        validate_recovery(item, run, steps, resource)


def test_recovery_refuses_changed_resource_and_completed_step():
    item, run, steps, resource = checkpoint()
    with pytest.raises(FixtureBlocked):
        validate_recovery(item, run, steps, str(uuid4()))
    steps[1].status = "completed"
    with pytest.raises(FixtureBlocked):
        validate_recovery(item, run, steps, resource)


@pytest.mark.parametrize("blocker", [None, "unresolved_write", "authority", "lease", "budget"])
async def test_recovery_preserves_run_steps_and_audits_explicit_retry(monkeypatch, blocker):
    from app.application.services.integration_foundation import IntegrationFoundation
    item, run, steps, resource = checkpoint()
    original_run = run.id
    authority = AsyncMock(side_effect=PermissionError("revoked") if blocker == "authority" else None)
    monkeypatch.setattr(IntegrationFoundation, "authorize", authority)
    unresolved = [] if blocker != "unresolved_write" else [SimpleNamespace(idempotency_key="another-write")]
    session = SimpleNamespace(scalar=AsyncMock(return_value=run), refresh=AsyncMock(), commit=AsyncMock(),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: unresolved)))
    runtime = SimpleNamespace(_load_steps=AsyncMock(return_value=steps),
        _load_context=AsyncMock(return_value=(None, None, None, SimpleNamespace(id=uuid4()))))
    coordinator = SimpleNamespace(acquire_lock=AsyncMock(return_value=None if blocker == "lease" else "lease"),
                                  renew_lock=AsyncMock(return_value=True),
                                  release_lock=AsyncMock())
    if blocker == "budget":
        item.payload["runtime"]["inspectionRecoveries"] = [{}] * 5
    if blocker:
        with pytest.raises((FixtureBlocked, PermissionError)):
            await resume_inspection(session, item, runtime, resource, coordinator)
        session.commit.assert_not_awaited()
        assert item.status == "uncertain_outcome"
    else:
        assert await resume_inspection(session, item, runtime, resource, coordinator) == 1
        assert run.id == original_run
        assert steps[0].status == "completed" and steps[1].status == "pending"
        assert item.payload["runtime"]["currentStep"] == 1
        assert item.payload["runtime"]["inspectionRecoveries"][0]["previousReason"] == "old inspection error"
        assert item.status == run.status == "running"
        session.commit.assert_awaited_once()


async def test_planner_refreshes_legacy_command_evidence_without_repeating_edits():
    planner = ScriptedPlanner(MANIFEST, "resource", str(uuid4()), True)
    observations = [{"title": f"[script:{step['id']}] action", "status": "completed", "providerOutput": {}}
                    for step in planner.plan["steps"][:10]]
    observations[7]["providerOutput"] = {"commandId": "existing-command"}
    observations[8]["providerOutput"] = {"status": "completed", "exitCode": 0}
    proposal = await planner.choose_next(tools=planner.tools(ExpandedGitHubProvider()), observations=observations)
    assert proposal.scope == "github.repository.workspace.command.status"
    assert proposal.action_input == {"commandId": "existing-command"}
    observations[8]["providerOutput"]["sourceVerificationAvailable"] = False
    with pytest.raises(FixtureBlocked, match="command will not replay"):
        await planner.choose_next(tools=[], observations=observations)


async def test_partial_recovery_preserves_completed_command_and_finish(monkeypatch):
    from app.application.services.integration_foundation import IntegrationFoundation
    item, run, steps, resource = checkpoint()
    item.status = run.status = "partial_completion"
    item.payload["runtime"].update(currentStep=3, resultId="previous-result")
    steps[1].status = "failed"
    steps[1].output = {"data": {"integrationFailure": {"executed": False, "code": "temporary_provider_error"}}}
    steps.append(SimpleNamespace(kind="finish", status="completed", input={}, output={"summary": "previous partial result"}))
    original_failure = steps[1].output
    monkeypatch.setattr(IntegrationFoundation, "authorize", AsyncMock())
    session = SimpleNamespace(scalar=AsyncMock(return_value=run), refresh=AsyncMock(), commit=AsyncMock(),
        scalars=AsyncMock(return_value=SimpleNamespace(all=list)))
    runtime = SimpleNamespace(_load_steps=AsyncMock(return_value=steps),
        _load_context=AsyncMock(return_value=(None, None, None, SimpleNamespace(id=uuid4()))))
    coordinator = SimpleNamespace(acquire_lock=AsyncMock(return_value="lease"), renew_lock=AsyncMock(return_value=True), release_lock=AsyncMock())
    assert await resume_inspection(session, item, runtime, resource, coordinator) == 1
    assert steps[0].status == steps[2].status == "completed"
    assert steps[1].status == "pending"
    meta = item.payload["runtime"]
    assert meta["currentStep"] == 1 and "resultId" not in meta
    assert meta["inspectionRecoveries"][0]["previousInspectionOutput"] == original_failure
    assert meta["inspectionRecoveries"][0]["previousResultId"] == "previous-result"


async def test_explicit_recovery_reinspects_command_once_without_launch():
    planner = ScriptedPlanner(MANIFEST, "resource", str(uuid4()), True)
    planner.verification_recovery_step = 9
    observations = [{"step": index + 1, "title": f"[script:{step['id']}] action", "status": "completed", "providerOutput": {}}
                    for index, step in enumerate(planner.plan["steps"][:10])]
    observations[7]["providerOutput"] = {"commandId": "original-command"}
    observations[8]["providerOutput"] = {"status": "completed", "exitCode": 0, "sourceVerificationAvailable": False}
    proposal = await planner.choose_next(tools=planner.tools(ExpandedGitHubProvider()), observations=observations)
    assert proposal.scope == "github.repository.workspace.command.status"
    assert proposal.action_input == {"commandId": "original-command"}
    observations.append({**observations[8], "step": 12})
    with pytest.raises(FixtureBlocked, match="command will not replay"):
        await planner.choose_next(tools=[], observations=observations)


@pytest.mark.parametrize("blocker", ["later_action", "unknown_certainty", "second_failure", "mutation"])
def test_partial_recovery_refuses_unsafe_history(blocker):
    item, run, steps, resource = checkpoint("repository.workspace.edit" if blocker == "mutation" else "repository.workspace.diff")
    item.status = run.status = "partial_completion"
    item.payload["runtime"]["currentStep"] = 3
    steps[1].status = "failed"
    steps[1].output = {"data": {"integrationFailure": {"executed": False}}}
    steps.append(SimpleNamespace(kind="finish", status="completed", input={}))
    if blocker == "later_action":
        steps[2].kind = "action"
        steps[2].input = {"title": "[script:commit] publish"}
    elif blocker == "unknown_certainty":
        steps[1].output = {}
    elif blocker == "second_failure":
        steps[0].status = "failed"
    with pytest.raises(FixtureBlocked):
        validate_recovery(item, run, steps, resource)
