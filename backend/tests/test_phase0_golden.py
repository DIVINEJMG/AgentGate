"""Phase 0 safety net: today's behaviour on the real outputs recorded in the live simulation
(tests/fixtures/sim/), pinned so every later change shows up as a reviewed difference.

Pinned:
- the step sentence each real output produces (`narrate`);
- what the planner is shown for each scenario's steps (`_planner_observations`);
- the planner prompt's sections and sizes for a fixed task.

When a change is intended, regenerate and review the difference:
    AGENTGATE_UPDATE_GOLDEN=1 python -m pytest tests/test_phase0_golden.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from audoryn_execution.application.context.narrator import narrate
from audoryn_execution.runtime.managed import ManagedRuntimeExecutor
from audoryn_shared.runtime.planner.adaptive import AdaptiveRuntimePlanner
from scripts.export_sim_fixtures import section_sizes

FIXTURES = Path(__file__).parent / "fixtures" / "sim"
GOLDEN = FIXTURES / "golden" / "phase0.json"


def evidence_steps(scenario: str) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((FIXTURES / "evidence" / scenario).glob("*.json"))]


def scenarios() -> list[str]:
    return [item["id"] for item in json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8"))]


def as_run_step(step: dict[str, Any]) -> Any:
    return SimpleNamespace(step_index=step["step"], status=step["status"], input=step["input"],
                           output=step["output"], kind="action")


def observations(scenario: str) -> list[dict[str, Any]]:
    executor = ManagedRuntimeExecutor.__new__(ManagedRuntimeExecutor)
    steps = [as_run_step(step) for step in evidence_steps(scenario)]
    return executor._planner_observations(cast(Any, steps))


def observation_shape(entry: dict[str, Any]) -> dict[str, Any]:
    provider = cast(dict[str, Any], entry.get("providerOutput") or {})
    data = provider.get("data")
    limits = provider.get("evidenceLimits") or {}
    return {
        "scope": entry["scope"], "summary": entry["summary"], "chars": len(json.dumps(entry, ensure_ascii=False)),
        "providerDataKeys": sorted(data) if isinstance(data, dict) else type(data).__name__,
        "truncated": bool(limits.get("truncated")), "omittedPaths": len(limits.get("omittedPaths") or []),
    }


def planner_prompt(observed: list[dict[str, Any]]) -> str:
    tools = [{"resourceId": "f6b836a3-d68a-441c-80ac-ee4938775f94", "resourceName": "DIVINEJMG/school",
              "provider": "github", "scope": "github.repository.issues.list", "operation": "repository.issues.list",
              "description": "List paginated issues with state and labels.", "risk": "low", "sideEffect": False,
              "approvalRecommendation": "none",
              "inputSchema": {"type": "object", "properties": {"state": {"enum": ["open", "closed", "all"]}},
                              "required": [], "additionalProperties": False},
              "authorityConstraints": {}, "defaultStartUrl": "", "allowedOrigins": []}]
    planner = AdaptiveRuntimePlanner(cast(Any, object()))
    return planner._prompt(
        job={"name": "List open issues", "objective": "List open issues in DIVINEJMG/school",
             "completionCriteria": ["Open issues are reported"], "workItemId": "181d53d6-0000-4000-8000-000000000000"},
        worker={"data": {"name": "Repo Issue Monitor"}}, trigger={"type": "manual"}, tools=tools,
        observations=observed, latest_browser=None, action_count=len(observed), max_actions=20,
        validation_feedback="", plan=[], plan_revisions_left=3)


def current() -> dict[str, Any]:
    narration = {f"{scenario}/{step['step']:02d}-{step['scope']}":
                 narrate(step["scope"], ((step["output"] or {}).get("data") or {}).get("output"),
                         step["input"].get("actionInput"))
                 for scenario in scenarios() for step in evidence_steps(scenario)}
    observed = {scenario: [observation_shape(entry) for entry in observations(scenario)] for scenario in scenarios()}
    prompt = planner_prompt(observations("s1_open_issues"))
    return {"narration": narration, "plannerObservations": observed,
            "plannerPrompt": {"chars": len(prompt), "sections": section_sizes(prompt)}}


def test_behaviour_matches_the_pinned_baseline() -> None:
    now = current()
    if os.environ.get("AGENTGATE_UPDATE_GOLDEN") == "1" or not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(now, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    pinned = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for part in ("narration", "plannerObservations", "plannerPrompt"):
        assert now[part] == pinned[part], (
            f"{part} changed. If intended, run with AGENTGATE_UPDATE_GOLDEN=1 and review the diff of {GOLDEN.name}.")
