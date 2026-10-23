"""Improvement plan, phase 3b: finishing is checked against each completion criterion, and the
planner keeps a short result for every earlier action."""
from __future__ import annotations

from typing import Any

import pytest

from audoryn_shared.application.context.evidence import planner_evidence
from audoryn_shared.runtime.planner.adaptive import AdaptiveRuntimePlanner, completion_check

OBSERVED = [{"step": 1, "scope": "bench.record.read", "status": "completed", "title": "Read a", "summary": "Record a: 42"},
            {"step": 2, "scope": "bench.record.read", "status": "completed", "title": "Read b", "summary": "Record b: 7"}]


@pytest.mark.parametrize(("raw", "expected"), [
    ([{"criterion": "Totals reported", "status": "met", "evidenceSteps": [1, 2, 2]}],
     [{"criterion": "Totals reported", "status": "met", "evidenceSteps": [1, 2]}]),
    # Claimed met without real evidence: not verifiable.
    ([{"criterion": "Totals reported", "status": "met", "evidenceSteps": [9]}],
     [{"criterion": "Totals reported", "status": "not_verifiable", "evidenceSteps": []}]),
    ([{"criterion": "Sent to finance", "status": "not_met", "evidenceSteps": []}],
     [{"criterion": "Sent to finance", "status": "not_met", "evidenceSteps": []}]),
    # Unknown status and blank criteria are not trusted.
    ([{"criterion": "X", "status": "probably", "evidenceSteps": [1]}, {"criterion": " ", "status": "met"}, "junk"],
     [{"criterion": "X", "status": "not_verifiable", "evidenceSteps": [1]}]),
    (None, []),
])
def test_completion_check_holds_claims_to_recorded_evidence(raw: Any, expected: list[dict[str, Any]]) -> None:
    assert completion_check(raw, OBSERVED) == expected


def test_reasoning_only_work_can_be_met_without_tool_evidence() -> None:
    assert completion_check([{"criterion": "Explain X", "status": "met", "evidenceSteps": []}], []) == [
        {"criterion": "Explain X", "status": "met", "evidenceSteps": []}]


@pytest.mark.parametrize(("criteria", "said", "expected"), [
    ([{"criterion": "c", "status": "met", "evidenceSteps": [1]}], "completed", "completed"),
    ([{"criterion": "c", "status": "not_met", "evidenceSteps": []}], "completed", "attention"),
    ([{"criterion": "c", "status": "met", "evidenceSteps": []}], "completed", "attention"),
    (None, "completed", "completed"),   # not assessed: unchanged behaviour
    (None, "attention", "attention"),
])
def test_a_finish_is_completed_only_when_every_criterion_is_met(criteria: Any, said: str, expected: str) -> None:
    planner = AdaptiveRuntimePlanner.__new__(AdaptiveRuntimePlanner)
    raw = {"decision": "finish", "summary": "Done", "resultStatus": said, **({"criteria": criteria} if criteria else {})}
    decision = planner._validate(raw, tools=[{"resourceId": "r", "scope": "bench.record.read"}],
                                 observations=OBSERVED, latest_browser=None)
    assert decision.result_status == expected


def test_the_finish_schema_asks_for_the_check() -> None:
    schema: Any = AdaptiveRuntimePlanner._schema([{"resourceId": "r", "scope": "s"}])
    item = schema["properties"]["criteria"]["items"]
    assert item["properties"]["status"]["enum"] == ["met", "not_met", "not_verifiable"]
    assert "criteria" not in schema["required"]  # an older model answer without it stays valid


def test_every_earlier_action_keeps_a_short_result() -> None:
    many = [{"step": index, "scope": "s", "status": "completed", "title": f"t{index}", "summary": f"found {index} " + "x" * 400}
            for index in range(1, 11)]
    evidence = planner_evidence(many)
    assert len(evidence["actionHistory"]) == 10 and len(evidence["recentEvidence"]) == 6
    assert evidence["actionHistory"][0]["result"].startswith("found 1 ")
    assert all(len(entry["result"]) <= 240 for entry in evidence["actionHistory"])
