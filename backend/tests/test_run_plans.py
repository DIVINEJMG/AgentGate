"""Saved run plans: the planner writes a plan with its first decision, every later call (whichever
model makes it) follows the same plan, it changes only through a counted, explained revision, and
the action limit follows the plan's size. End-to-end runs are in the offline benchmark
(planned-30-steps, model-switch)."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from app.application.context.evidence import planner_evidence
from app.application.services.task_activity import activity_for
from app.bootstrap.settings import settings
from app.runtime.managed import _plan_progress, _plan_view, action_budget
from app.runtime.planner.adaptive import (
    AdaptivePlanDecision,
    AdaptiveRuntimePlanner,
    current_plan_step,
    normalize_plan,
)

TOOLS: list[dict[str, object]] = [{"resourceId": "r", "scope": "bench.record.read"},
                                  {"resourceId": "r", "scope": "bench.record.write"}]
SCOPES = {"bench.record.read", "bench.record.write"}


def step(title: str, scope: str = "bench.record.read", depends: list[int] | None = None) -> dict[str, object]:
    return {"title": title, "goal": "", "scope": scope, "target": "", "dependsOn": depends or [], "check": ""}


def test_a_plan_is_numbered_and_checked_against_the_authorized_tools() -> None:
    plan = normalize_plan([step("Read a"), step("Write b", "bench.record.write", [1, 5]), step("Report", "")], scopes=SCOPES)
    assert [entry["position"] for entry in plan] == [1, 2, 3]
    assert plan[1]["dependsOn"] == [1]  # a dependency on a later or missing step is dropped
    with pytest.raises(RuntimeError, match="not an authorized tool"):
        normalize_plan([step("Delete everything", "bench.record.delete")], scopes=SCOPES)
    with pytest.raises(RuntimeError, match="Write the plan first"):
        normalize_plan([], scopes=SCOPES)


def test_a_plan_is_limited_in_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "runtime_max_plan_steps", 3)
    with pytest.raises(RuntimeError, match="the limit is 3"):
        normalize_plan([step(str(index)) for index in range(4)], scopes=SCOPES)


def test_the_current_step_is_the_first_pending_one_whose_dependencies_are_done() -> None:
    plan = normalize_plan([step("A"), step("B", depends=[1]), step("C")], scopes=SCOPES)
    assert current_plan_step(plan)["position"] == 1  # type: ignore[index]
    plan[0]["status"] = "done"
    assert current_plan_step(plan)["position"] == 2  # type: ignore[index]
    plan[0]["status"], plan[2]["status"] = "pending", "done"
    assert current_plan_step(plan)["position"] == 1  # type: ignore[index]


ACT = AdaptivePlanDecision(decision="act", summary="s", title="t", instruction="i", resource_id="r",
                           scope="bench.record.read", action_input={"key": "a"})
FINISH = AdaptivePlanDecision(decision="finish", summary="s", title="t", instruction="i", resource_id="",
                              scope="", action_input={})


def test_the_first_decision_writes_the_plan_and_names_its_step() -> None:
    attach = AdaptiveRuntimePlanner._with_plan
    with pytest.raises(RuntimeError, match="Write the plan first"):
        attach(ACT, {}, tools=TOOLS, plan=[], plan_revisions_left=3)
    # A missing step number means the current step; a wrong one is sent back.
    assert attach(ACT, {"plan": [step("Read a")]}, tools=TOOLS, plan=[], plan_revisions_left=3).plan_step == 1
    with pytest.raises(RuntimeError, match="Set planStep"):
        attach(ACT, {"plan": [step("Read a")], "planStep": 5}, tools=TOOLS, plan=[], plan_revisions_left=3)
    decision = attach(ACT, {"plan": [step("Read a"), step("Write b", "bench.record.write")], "planStep": 1,
                            "planStepDone": True}, tools=TOOLS, plan=[], plan_revisions_left=3)
    assert len(decision.plan) == 2 and decision.plan_step == 1 and decision.plan_step_done
    # A run that finishes at once (nothing to do) needs no plan.
    assert attach(FINISH, {}, tools=TOOLS, plan=[], plan_revisions_left=3).plan == []


def test_the_plan_changes_only_through_an_explained_counted_revision() -> None:
    attach = AdaptiveRuntimePlanner._with_plan
    saved = normalize_plan([step("Read a"), step("Write b", "bench.record.write")], scopes=SCOPES)
    saved[0]["status"] = "done"
    with pytest.raises(RuntimeError, match="planRevisionReason"):
        attach(ACT, {"plan": [step("Read c")], "planStep": 3}, tools=TOOLS, plan=saved, plan_revisions_left=3)
    with pytest.raises(RuntimeError, match="no longer be revised"):
        attach(ACT, {"plan": [step("Read c")], "planRevisionReason": "x", "planStep": 3}, tools=TOOLS,
               plan=saved, plan_revisions_left=0)
    revised = attach(ACT, {"plan": [step("Read c")], "planRevisionReason": "The test failed", "planStep": 3},
                     tools=TOOLS, plan=saved, plan_revisions_left=1)
    assert [entry["position"] for entry in revised.plan] == [3]  # numbering continues after the old steps
    assert revised.plan_revision_reason == "The test failed"
    with pytest.raises(RuntimeError, match=r"Pending steps: \[2\]"):  # a done step cannot be worked on again
        attach(ACT, {"planStep": 1}, tools=TOOLS, plan=saved, plan_revisions_left=3)


def test_every_later_call_sees_the_same_saved_plan_whichever_model_makes_it() -> None:
    planner = AdaptiveRuntimePlanner(cast(Any, None))
    saved = normalize_plan([step("Read a"), step("Write b", "bench.record.write", [1])], scopes=SCOPES)
    saved[0]["status"] = "done"
    prompt = planner._prompt(job={}, worker={}, trigger={}, tools=TOOLS, observations=[], latest_browser=None,
                             action_count=1, max_actions=20, validation_feedback="", plan=saved, plan_revisions_left=2)
    assert "SAVED PLAN (same for every model)\n1. [done] Read a\n2. [pending] Write b" in prompt
    assert '"position": 2' in prompt and "2 revisions left" in prompt
    first = planner._prompt(job={}, worker={}, trigger={}, tools=TOOLS, observations=[], latest_browser=None,
                            action_count=0, max_actions=20, validation_feedback="", plan=[], plan_revisions_left=3)
    assert "None yet. Also return `plan`" in first
    assert "plan" in planner._schema(TOOLS, plan_required=True)["required"]  # type: ignore[operator]


def test_evidence_follows_the_current_step_and_older_history_shrinks() -> None:
    observations = [{"step": index, "scope": "bench.record.read", "status": "completed", "title": f"Read {index}",
                     "summary": f"value {index}", "actionFingerprint": "f" * 64, "payload": "x" * 50}
                    for index in range(1, 21)]
    evidence = planner_evidence(observations, relevant={3})
    assert [item["data"]["step"] for item in evidence["recentEvidence"]] == [20, 19, 3]  # the latest, then the step's own
    assert "actionFingerprint" not in evidence["actionHistory"][0] and len(evidence["actionHistory"]) == 20
    assert len(planner_evidence(observations)["recentEvidence"]) == 6  # without a plan: as before


def test_the_action_limit_follows_the_plan() -> None:
    assert action_budget(0) == settings.runtime_max_action_steps == 20
    assert action_budget(5) == 20 and action_budget(30) == 60
    assert action_budget(60) == settings.runtime_max_plan_actions == 120


def _row(position: int, title: str, status: str = "pending") -> Any:
    return SimpleNamespace(position=position, title=title, goal="", scope="", target=f"src/{position}.py",
                           depends_on=[], check="", status=status, action_steps=[])


def test_progress_is_read_from_the_runs_own_steps_without_writing_the_plan() -> None:
    rows = [_row(1, "Edit users API"), _row(2, "Run tests")]
    steps = [SimpleNamespace(kind="action", status="completed", step_index=0, input={"planStep": 1, "planStepDone": False}),
             SimpleNamespace(kind="action", status="completed", step_index=1, input={"planStep": 1, "planStepDone": True}),
             SimpleNamespace(kind="action", status="failed", step_index=2, input={"planStep": 2})]
    view = _plan_view(cast(Any, rows), cast(Any, steps))
    assert [(entry["status"], entry["actionSteps"]) for entry in view] == [("done", [1, 2]), ("pending", [3])]
    assert _plan_progress(view) == {"done": 1, "total": 2, "position": 2, "title": "Run tests", "target": "src/2.py"}


def test_the_workspace_shows_where_the_run_is_in_its_plan() -> None:
    now = datetime.now(UTC)
    item = SimpleNamespace(id=uuid4(), status="running", created_at=now, updated_at=now, payload={"runtime": {
        "queueState": "executing", "planProgress": {"done": 6, "total": 23, "position": 7,
                                                    "title": "Edit users API", "target": "src/api/users.ts"}}})
    activity = activity_for(item, None, [], now)
    assert activity["detail"] == "Step 7 of 23: Edit users API (src/api/users.ts)"
    assert activity["plan"]["total"] == 23


def test_plan_problems_never_block_a_run_after_one_correction() -> None:
    attach = AdaptiveRuntimePlanner._with_plan
    # The first decision forgot the plan twice: the action still runs, without a plan.
    assert attach(ACT, {}, tools=TOOLS, plan=[], plan_revisions_left=3, lenient=True).plan == []
    saved = normalize_plan([step("Read a"), step("Write b", "bench.record.write")], scopes=SCOPES)
    saved[0]["status"] = "done"
    # A wrong or missing step number falls back to the current step.
    assert attach(ACT, {"planStep": 1}, tools=TOOLS, plan=saved, plan_revisions_left=3, lenient=True).plan_step == 2
    # A revision without a reason is ignored rather than failing the action.
    kept = attach(ACT, {"plan": [step("Other")], "planStep": 2}, tools=TOOLS, plan=saved, plan_revisions_left=3, lenient=True)
    assert kept.plan == [] and kept.plan_step == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("one_shot", "status"), [(True, "archived"), (False, "active")])
async def test_a_finished_one_off_task_leaves_the_active_jobs(tmp_path: Any, one_shot: bool, status: str) -> None:
    import sqlite3
    from dataclasses import replace

    from scripts import runtime_benchmark as bench
    task = replace(next(item for item in bench.TASKS if item.name == "single-read"), one_shot=one_shot)
    result = await bench.run_task(task, tmp_path)
    assert result["outcome"]["workItem"] == "completed"
    connection = sqlite3.connect(str(tmp_path / f"{task.name}.db"))
    assert connection.execute("select status from jobs").fetchone()[0] == status
    connection.close()
