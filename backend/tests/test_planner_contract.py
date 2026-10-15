"""Phase 2 planner contract: the context planner for planning (rules and tools from facts, F7), plan
revision numbering and kept work (F2), the completion-criteria check on finish (F24) and plain
corrections (F13)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from app.application.context.corrections import schema_correction
from app.application.context.planning import PlanningFacts, authorized_tools, planning_rules
from app.runtime.managed import _plan_view
from app.runtime.planner.adaptive import (
    AdaptivePlanDecision,
    AdaptiveRuntimePlanner,
    normalize_plan,
    unassessed,
)

WORK_ITEM = "55ea0379-0000-4000-8000-000000000000"
GITHUB_TOOLS: list[dict[str, object]] = [
    {"resourceId": "repo", "resourceName": "DIVINEJMG/school", "provider": "github", "scope": "github.repository.branch.read",
     "operation": "repository.branch.read", "description": "Read a branch.", "risk": "low", "sideEffect": False,
     "approvalRecommendation": "none", "authorityConstraints": {}, "defaultStartUrl": "", "allowedOrigins": [],
     "inputSchema": {"type": "object", "properties": {"branch": {"type": "string"}}, "required": ["branch"],
                     "additionalProperties": False}},
    {"resourceId": "repo", "resourceName": "DIVINEJMG/school", "provider": "github", "scope": "github.repository.branch.create",
     "operation": "repository.branch.create", "description": "Create a branch.", "risk": "medium", "sideEffect": True,
     "approvalRecommendation": "required", "authorityConstraints": {}, "defaultStartUrl": "", "allowedOrigins": [],
     "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
]


def test_rules_follow_the_authorized_tools() -> None:  # F7, A6
    github = planning_rules(PlanningFacts.of(GITHUB_TOOLS, browser_open=False, work_item_id=WORK_ITEM))
    assert "codex/55ea0379/<short-task-name>" in github  # the task's own prefix, not a description of it
    assert "browser" not in github.replace("Browser sessionId", "")  # no browser rules without a browser tool
    read_only = planning_rules(PlanningFacts.of(GITHUB_TOOLS[:1], browser_open=False, work_item_id=WORK_ITEM))
    assert "codex/" not in read_only  # a task that cannot write gets no branch rule
    browser = planning_rules(PlanningFacts.of([{"scope": "browser.page.read"}], browser_open=True))
    assert "A browser page is available" in browser and "browser.element.press_key" in browser
    assert "No external tools are available" in planning_rules(PlanningFacts.of([], browser_open=False))


def test_tools_are_shown_once_per_resource_without_empty_fields() -> None:
    [resource] = authorized_tools(GITHUB_TOOLS)
    assert resource["resourceId"] == "repo" and resource["resourceName"] == "DIVINEJMG/school"
    read, create = resource["tools"]
    assert read == {"scope": "github.repository.branch.read", "description": "Read a branch.", "risk": "low",
                    "input": {"properties": {"branch": {"type": "string"}}, "required": ["branch"]}}
    assert create["writes"] is True and create["approval"] == "required" and "input" not in create
    assert len(json.dumps(authorized_tools(GITHUB_TOOLS))) < len(json.dumps(GITHUB_TOOLS)) / 2


TOOLS: list[dict[str, object]] = [{"resourceId": "r", "scope": "bench.record.read"},
                                  {"resourceId": "r", "scope": "bench.record.write"}]
SCOPES = {"bench.record.read", "bench.record.write"}
ACT = AdaptivePlanDecision(decision="act", summary="s", title="t", instruction="i", resource_id="r",
                           scope="bench.record.read", action_input={"key": "a"})


def step(title: str) -> dict[str, object]:
    return {"title": title, "goal": "", "scope": "bench.record.read", "target": "", "dependsOn": [], "check": ""}


def test_a_revision_keeps_completed_work_and_says_how_it_is_numbered() -> None:  # F2, A1, A2
    saved = normalize_plan([step("List issues"), step("Summarise")], scopes=SCOPES)
    saved[0]["completedActions"] = [1]  # its action completed, but the model left planStepDone out
    prompt = AdaptiveRuntimePlanner(cast(Any, None))._prompt(
        job={}, worker={}, trigger={}, tools=TOOLS, observations=[], latest_browser=None, action_count=1,
        max_actions=20, validation_feedback="", plan=saved, plan_revisions_left=2)
    assert "1. [pending; action 1 completed, not marked done] List issues" in prompt
    assert "numbered from 3 in the order sent" in prompt
    revised = AdaptiveRuntimePlanner._with_plan(
        ACT, {"plan": [step("Read issue #2"), step("Summarise")], "planRevisionReason": "Need the body", "planStep": 3},
        tools=TOOLS, plan=saved, plan_revisions_left=2)
    assert [entry["position"] for entry in revised.plan] == [3, 4] and revised.plan_step == 3
    with pytest.raises(RuntimeError, match="Your revised steps are numbered 3 to 4"):
        AdaptiveRuntimePlanner._with_plan(ACT, {"plan": [step("Read issue #2"), step("Summarise")], "planRevisionReason": "x",
                                                "planStep": 2}, tools=TOOLS, plan=saved, plan_revisions_left=2)


def _row(position: int, status: str = "pending") -> Any:
    return SimpleNamespace(position=position, title=f"Step {position}", goal="", scope="", target="",
                           depends_on=[], check="", status=status, action_steps=[])


def test_the_plan_view_reports_completed_actions_and_kept_steps() -> None:
    steps = [SimpleNamespace(kind="action", status="completed", step_index=0, input={"planStep": 1}),
             SimpleNamespace(kind="action", status="failed", step_index=1, input={"planStep": 2})]
    view = _plan_view(cast(Any, [_row(1), _row(2), _row(3, "done")]), cast(Any, steps))
    assert [(entry["status"], entry["completedActions"]) for entry in view] == [
        ("pending", [1]), ("pending", []), ("done", [])]  # a step a revision kept stays done


FINISH = {"decision": "finish", "summary": "Done.", "title": "Finish", "instruction": "Report."}
OBSERVED = [{"step": 1, "scope": "bench.record.read", "status": "completed"}]
CRITERIA = ["Record a was read", "The value is reported"]


def test_a_finish_must_assess_every_criterion() -> None:  # F24
    planner = AdaptiveRuntimePlanner(cast(Any, None))

    def finish(raw: dict[str, object], last_call: bool = False) -> AdaptivePlanDecision:
        return planner._validate(raw, tools=TOOLS, observations=OBSERVED, latest_browser=None,
                                 criteria=CRITERIA, last_call=last_call)

    with pytest.raises(RuntimeError, match="Missing: 'Record a was read'; 'The value is reported'"):
        finish({**FINISH, "criteria": []})
    unassessed_finish = finish({**FINISH, "criteria": []}, last_call=True)
    assert unassessed_finish.result_status == "attention"
    assert [entry["status"] for entry in unassessed_finish.criteria] == ["not_verifiable", "not_verifiable"]
    reworded = [{"criterion": "record A read", "status": "met", "evidenceSteps": [1]},
                {"criterion": "value shown to the person", "status": "met", "evidenceSteps": [1]}]
    assert finish({**FINISH, "criteria": reworded}).result_status == "completed"


def test_unassessed_matches_by_text_then_by_count() -> None:
    assert unassessed(CRITERIA, [{"criterion": "The value is reported."}]) == ["Record a was read"]
    assert unassessed(CRITERIA, [{"criterion": "x"}, {"criterion": "y"}]) == []
    assert unassessed([], []) == []


def test_corrections_name_the_wrong_value_and_the_choices() -> None:  # F13
    schema = AdaptiveRuntimePlanner._schema([{"resourceId": "repo", "scope": "github.repository.pull_requests.list"}])
    answer = {"decision": "act", "resultStatus": "completed", "summary": "s", "title": "t", "instruction": "i",
              "resourceId": "repo", "scope": "github.repository.issues.create", "input": {}}
    message = schema_correction(Draft202012Validator(schema).iter_errors(answer))
    assert message.startswith("`scope` 'github.repository.issues.create' is not allowed here.")
    assert "Allowed: github.repository.pull_requests.list." in message
    assert '"summary"' not in message  # the rejected answer is not echoed
    preparation = {"type": "object", "properties": {"scopes": {"type": "array", "items": {
        "enum": ["github.repository.pull_requests.list", "github.repository.issues.list"]}}}}
    wrong = schema_correction(Draft202012Validator(preparation).iter_errors({"scopes": ["github.repository.pulls.list"]}))
    assert "Closest: 'github.repository.pull_requests.list'" in wrong


def test_an_unauthorized_tool_is_named_with_the_authorized_ones() -> None:  # F13
    planner = AdaptiveRuntimePlanner(cast(Any, None))
    raw = {"decision": "act", "summary": "s", "title": "t", "instruction": "i", "resourceId": "r",
           "scope": "bench.record.delete", "input": {}}
    with pytest.raises(RuntimeError, match=r"`scope` 'bench.record.delete' is not allowed here\. .*Allowed: "
                                           r"bench.record.read, bench.record.write\."):
        planner._validate(raw, tools=TOOLS, observations=[], latest_browser=None)


FIXTURES = Path(__file__).parent / "fixtures" / "sim"


def test_the_recorded_scenario_7_finish_is_asked_for_its_criteria() -> None:  # F24, simulator request 0047
    rows = [json.loads(line) for line in (FIXTURES / "transcripts" / "s7_event_trigger.jsonl").read_text(encoding="utf-8").splitlines()]
    [answer, *_] = [row["answer"]["content"] for row in rows
                    if row["schema_name"] == "audoryn_next_action" and row["answer"]["content"].get("decision") == "finish"]
    assert answer["criteria"] == [] and answer["resultStatus"] == "completed"  # accepted as completed before
    job_criteria = ["The new issue is detected via the GitHub issues.opened event",
                    "The issue is read to capture its number, title, labels, and description",
                    "A concise summary of the issue is posted to this conversation"]
    planner = AdaptiveRuntimePlanner(cast(Any, None))
    with pytest.raises(RuntimeError, match="Assess every completion criterion"):
        planner._validate(answer, tools=TOOLS, observations=OBSERVED, latest_browser=None, criteria=job_criteria)
    assert planner._validate(answer, tools=TOOLS, observations=OBSERVED, latest_browser=None, criteria=job_criteria,
                             last_call=True).result_status == "attention"


def test_a_replayed_answer_uses_the_branch_prefix_its_prompt_gives() -> None:
    from scripts.sim_replay import adapted

    record = {"messages": [{"content": "- This task's GitHub branches start with codex/ac73fcb3/: name ..."}]}
    answer = {"content": {"input": {"branch": "codex/55ea0379/readme-setup"}}}
    assert adapted(answer, record) == {"content": {"input": {"branch": "codex/ac73fcb3/readme-setup"}}}
    assert adapted(answer, {"messages": []}) == answer


def test_constraints_every_tool_shares_are_shown_once_per_resource() -> None:
    granted = {"branches": ["codex/55ea0379/readme-setup"], "pullRequests": [8]}
    [resource] = authorized_tools([{**tool, "authorityConstraints": granted} for tool in GITHUB_TOOLS])
    assert resource["authorityConstraints"] == granted
    assert all("authorityConstraints" not in tool for tool in resource["tools"])


def test_a_replay_leaves_other_tasks_planning_requests_alone() -> None:
    from scripts.sim_replay import other_task

    mine, theirs = "53fe95b9-48d3-43d3-92c8-990a0012ecba", "9987cdbb-0000-4000-8000-000000000000"
    def request(item: str) -> dict:
        return {"schema_name": "audoryn_next_action", "messages": [{"content": f'JOB\n{{"workItemId": "{item}"}}'}]}
    assert other_task(request(mine), {"item": None}) is None  # not known yet: wait
    assert other_task(request(theirs), {"item": mine}) is True
    assert other_task(request(mine), {"item": mine}) is False
    assert other_task({"schema_name": "integration_task", "messages": []}, {"item": None}) is False
