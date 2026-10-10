import json
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.github_scripted_planner import FixtureBlocked, ScriptedPlanner
from scripts.simulate_github_workflow import live, offline

MANIFEST = Path(__file__).resolve().parents[1] / "scripts/fixtures/github_workflow/plan.json"


def arguments(directory, scenario="missing-files", **extra):
    return Namespace(state_dir=directory, scenario=scenario, manifest=MANIFEST,
                     approve_fixture=False, max_steps=100, stop_after=None, **extra)


@pytest.mark.asyncio
async def test_offline_workflow_runs_actual_tests_and_reuses_completed_actions(tmp_path):
    args = arguments(tmp_path)
    assert await offline(args) == 0
    before = json.loads((tmp_path / "fake-provider.json").read_text())
    assert before["command"]["exitCode"] == 0
    assert "Ran 2 tests" in before["command"]["stderr"]
    assert before["writes"] == 3
    assert before["prs"]["1"]["draft"] is True
    assert await offline(args) == 0
    assert json.loads((tmp_path / "fake-provider.json").read_text()) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["uncertain-write", "approval"])
async def test_recovery_preserves_completed_writes(tmp_path, scenario):
    args = arguments(tmp_path, scenario)
    assert await offline(args) == 2
    args.approve_fixture = True
    assert await offline(args) == 0
    assert json.loads((tmp_path / "fake-provider.json").read_text())["writes"] == 3


@pytest.mark.asyncio
async def test_interruption_and_branch_race(tmp_path):
    args = arguments(tmp_path)
    args.stop_after = 7
    assert await offline(args) == 2
    args.stop_after = None
    assert await offline(args) == 0
    race = arguments(tmp_path / "race", "branch-race")
    assert await offline(race) == 2
    external = json.loads((race.state_dir / "fake-provider.json").read_text())
    assert external["writes"] == 1 and not external["prs"]


@pytest.mark.asyncio
async def test_bad_test_result_and_unavailable_scope_cannot_publish(tmp_path):
    planner = ScriptedPlanner(MANIFEST, "resource", "12345678-0000-0000-0000-000000000000", True)
    with pytest.raises(RuntimeError, match="unavailable"):
        await planner.choose_next(tools=[], observations=[])
    with pytest.raises(FixtureBlocked, match="did not pass"):
        await planner.choose_next(tools=[], observations=[{
            "title": "[script:test_status] test", "status": "completed",
            "providerOutput": {"status": "completed", "exitCode": 1},
        }])


@pytest.mark.asyncio
async def test_live_requires_explicit_flags_before_loading_database():
    args = Namespace(work_item_id=None, resource_id=None, organization_id=None,
                     allow_coding=False, workers_stopped=False)
    with pytest.raises(FixtureBlocked, match="requires exact"):
        await live(args)


def test_preparation_requires_complete_authority_and_preserves_constraints():
    from scripts.github_workflow_preparation import fixture_binding, fixture_scopes
    plan = json.loads(MANIFEST.read_text())
    resource = Namespace(id="resource", connection_id="connection", provider="github",
                         configuration={"repository": "DIVINEJMG/AgentGate"}, display_name="AgentGate")
    grant = Namespace(scopes=fixture_scopes(plan), destinations={"paths": list(plan["files"])},
                      constraints={"branchPrefix": "codex/"})
    binding = fixture_binding(resource, grant, plan)
    assert binding["scopes"] == fixture_scopes(plan)
    assert binding["destinations"] == grant.destinations
    binding["destinations"]["paths"].append("unrelated")
    assert "unrelated" not in grant.destinations["paths"]
    grant.scopes = ["github.repository.metadata.read"]
    with pytest.raises(FixtureBlocked, match="Source authority lacks"):
        fixture_binding(resource, grant, plan)
    resource.configuration["repository"] = "DIVINEJMG/Divine-JMG"
    with pytest.raises(FixtureBlocked, match="fixture repository"):
        fixture_binding(resource, grant, plan)


@pytest.mark.asyncio
async def test_preparation_requires_explicit_flags_before_database_access():
    from scripts.github_workflow_preparation import prepare
    with pytest.raises(FixtureBlocked, match="Preparation requires"):
        await prepare(Namespace(source_work_item_id=None, organization_id=None,
                                preparation_key=None, workers_stopped=False))


def test_live_runner_honors_saved_retry_time():
    from datetime import UTC, datetime, timedelta

    from scripts.simulate_github_workflow import saved_retry_delay
    now = datetime.now(UTC)
    item = Namespace(status="queued", payload={"runtime": {"providerRetryAt": (now + timedelta(seconds=60)).isoformat()}})
    assert saved_retry_delay(item, now) == 60
    assert saved_retry_delay(item, now + timedelta(seconds=70)) == 0
    item.status = "running"
    assert saved_retry_delay(item, now) == 0
