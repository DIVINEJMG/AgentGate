import hashlib
import json
from typing import cast

import pytest

from app.application.services.task_activity import action_phase
from app.domain.ai.providers import AIGateway
from app.runtime.evidence import planner_evidence
from app.runtime.planner.adaptive import AdaptiveRuntimePlanner, _repeated_integration_read


def observation(scope="github.repository.tree.read", inputs=None, resource="repo"):
    fingerprint = hashlib.sha256(json.dumps({
        "scope": scope, "resource": resource, "input": inputs or {},
    }, sort_keys=True, default=str).encode()).hexdigest()
    return {"step": 1, "scope": scope, "resourceId": resource,
            "actionFingerprint": fingerprint, "verification": {"verified": True}}


def test_large_trees_preserve_all_history_and_latest_evidence():
    observations = [
        {**observation(), "step": index, "providerOutput": {
            "data": {"sha": str(index), "tree": [{"path": "x" * 1000}] * 675},
        }} for index in range(1, 10)
    ]
    result = planner_evidence(observations)
    assert [item["step"] for item in result["actionHistory"]] == list(range(1, 10))
    assert result["recentEvidence"][0]["data"]["step"] == 9
    assert result["recentEvidence"][0]["evidenceLimits"]["truncated"]
    assert len(json.dumps(result)) < 24000
    assert "identical full read" in result["instruction"]


def test_identical_verified_read_is_rejected_but_new_input_or_resource_is_not():
    previous = observation(inputs={"sha": "a" * 40, "recursive": True})
    assert _repeated_integration_read(previous["scope"], previous["actionFingerprint"], [previous])
    for changed in (observation(inputs={"sha": "b" * 40}), observation(resource="other")):
        assert not _repeated_integration_read(changed["scope"], changed["actionFingerprint"], [previous])
    previous["verification"]["verified"] = False
    assert not _repeated_integration_read(previous["scope"], previous["actionFingerprint"], [previous])


@pytest.mark.parametrize("scope", [
    "github.repository.workspace.command.status", "github.repository.workflow.run.read",
    "github.repository.checks.read", "browser.page.read",
])
def test_polling_and_browser_guards_remain_separate(scope):
    previous = observation(scope)
    assert not _repeated_integration_read(scope, previous["actionFingerprint"], [previous])


def test_mutation_allows_reinspection():
    previous = observation()
    assert not _repeated_integration_read(previous["scope"], previous["actionFingerprint"], [
        previous, observation("github.repository.workspace.edit"),
    ])


def test_validation_rejects_repeat_before_execution():
    previous = observation(inputs={"sha": "a" * 40})
    raw = {"decision": "act", "summary": "Inspect", "title": "Read tree",
           "instruction": "Inspect tree", "resourceId": "repo",
           "scope": previous["scope"], "input": {"sha": "a" * 40}}
    with pytest.raises(RuntimeError, match="narrower path/page/segment"):
        AdaptiveRuntimePlanner(cast(AIGateway, None))._validate(raw, tools=[{
            "resourceId": "repo", "scope": previous["scope"], "inputSchema": {},
        }], observations=[previous], latest_browser=None)


def test_progress_distinguishes_repository_reads_and_workspace():
    labels = [action_phase(scope)[1] for scope in (
        "github.repository.metadata.read", "github.repository.branch.read",
        "github.repository.tree.read", "github.repository.contents.read",
        "github.repository.workspace.open", "github.repository.workspace.diff",
    )]
    assert len(set(labels)) == len(labels)
