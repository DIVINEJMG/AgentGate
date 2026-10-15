"""Phase 1 context engine: evidence compacter (F1, F3, F6), step narrator (F5), git-format
workspace diff (F9) and prompt measurement, on the real outputs recorded in the live simulation."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.application.context.evidence import as_list, as_map, compact_output
from app.application.context.narrator import narrate
from app.application.context.sections import Section, assemble
from app.execution.coding.patch import workspace_patch
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.observability.usage import metered
from tests.test_github_expanded import ScriptedHTTP, request
from tests.test_phase0_golden import evidence_steps, observations


def provider_data(scenario: str, index: int) -> dict[str, Any]:
    return as_map(as_map(observations(scenario)[index]["providerOutput"])["data"])


def test_a_list_result_keeps_its_items():  # F1
    data = provider_data("s1_open_issues", 0)
    assert data["count"] == 1
    assert data["items"] == [{
        "number": 2, "title": "TE", "state": "open", "labels": [], "comments": 2, "assignees": [],
        "user": "DIVINEJMG", "html_url": "https://github.com/DIVINEJMG/school/issues/2",
        "updated_at": "2026-10-14T15:36:11Z",
    }]


def test_a_shortened_list_body_is_marked_as_cut():  # F1: never complete-looking when cut
    issues = [{"number": n, "title": f"Issue {n}", "body": "x" * 1000, "labels": [{"name": "bug", "color": "f00"}]}
              for n in range(3)]
    evidence = compact_output("github.repository.issues.list", issues)
    items = [as_map(item) for item in as_list(as_map(evidence["data"])["items"])]
    assert [item["labels"] for item in items] == [["bug"]] * 3
    assert all(len(str(item["body"])) == 301 for item in items)
    limits = as_map(evidence["evidenceLimits"])
    assert limits["truncated"] and "output.items[0].body" in as_list(limits["omittedPaths"])


def test_issue_evidence_drops_api_noise():  # F3
    raw = evidence_steps("s1_open_issues")[1]["output"]["data"]["output"]
    shown = json.dumps(provider_data("s1_open_issues", 1))
    assert "api.github.com" not in shown and "reactions" not in shown and "sub_issues_summary" not in shown
    assert provider_data("s1_open_issues", 1)["user"] == "DIVINEJMG"
    assert len(shown) < len(json.dumps(raw)) / 3


def test_pull_request_evidence_keeps_identity_and_refs():
    data = provider_data("s5_clarification", 0)
    assert data["node_id"] and data["draft"] is True and data["number"] == 8
    assert as_map(data["head"])["ref"] == "codex/55ea0379/readme-setup"
    assert as_map(data["base"])["repo"] == "DIVINEJMG/school"
    assert len(json.dumps(data)) < 3000  # 16 KB raw


def test_a_write_shows_the_values_it_confirmed():  # F6
    evidence = compact_output(
        "github.repository.issues.create", {"number": 7, "title": "Simulation test"},
        action_input={"title": "Simulation test", "body": "Made by a simulated run.", "labels": ["x"]},
        confirmed_fields=["title", "body"])
    assert as_map(evidence["data"])["confirmedWritten"] == {"title": "Simulation test", "body": "Made by a simulated run."}


async def test_github_reports_the_fields_its_reread_matched():  # F6
    marker = "<!-- audoryn-action:" + hashlib.sha256(b"durable-action").hexdigest() + " -->"
    issue = {"id": 99, "number": 1, "title": "Fix", "body": "Explain\n\n" + marker, "state": "open",
             "labels": [{"name": "bug"}], "html_url": "https://github.com/owner/repo/issues/1"}
    provider = ExpandedGitHubProvider(http=ScriptedHTTP([issue, issue]))
    result = await provider.execute(
        request=request(provider, "repository.issue.create", {"title": "Fix", "body": "Explain", "labels": ["bug"]}),
        configuration={"repository": "owner/repo"}, credential="token")
    assert result.verification.details["confirmedFields"] == ["title", "body", "labels"]


def test_workspace_export_lists_changes_without_repeating_their_content():
    data = provider_data("s3_coding_draft_pr", 6)
    assert data["changes"] == [{"path": "README.md", "chars": 296}, {"path": "scripts/setup.sh", "chars": 368}]


@pytest.mark.parametrize(("scope", "output", "action_input", "expected"), [
    ("github.repository.issues.create", {"number": 7, "title": "Simulation test"}, {}, "Created issue #7 Simulation test."),
    ("github.repository.issue.comment.create", {"id": 5, "body": "Done"}, {"number": 7}, "Created comment on #7."),
    ("github.repository.issue.labels.set", [{"name": "bug"}], {"number": 7}, "Set label on #7: bug."),
    ("github.repository.label.update", {"name": "bug"}, {"name": "old"}, "Updated label bug."),
    ("github.repository.workspace.edit", {"path": "README.md", "diffStat": "1 file changed"}, {},
     "Edited README.md (1 file changed)."),
    ("github.repository.pull_request.create", {"number": 8, "title": "Setup", "draft": True}, {},
     "Opened draft pull request #8 Setup."),
    ("github.repository.pull_request.ready", {}, {"number": 8}, "Marked pull request #8 ready for review."),
    ("github.repository.branch.create", {"ref": "refs/heads/codex/1/x", "object": {"sha": "abcdef123"}}, {},
     "Created branch codex/1/x at abcdef1."),
    ("browser.page.read", {"observation": {"url": "https://example.com"}}, {}, None),
])
def test_a_step_is_described_by_what_it_did(scope, output, action_input, expected):  # F5
    assert narrate(scope, output, action_input) == expected


def test_recorded_writes_no_longer_read_as_reads():  # F5, on the live outputs
    for scenario, index in (("s2_create_issue", 0), ("s3_coding_draft_pr", 4), ("s3_coding_draft_pr", 9)):
        step = evidence_steps(scenario)[index]
        sentence = narrate(step["scope"], step["output"]["data"]["output"], step["input"]["actionInput"])
        assert sentence and not sentence.startswith(("Read", "Found"))


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_workspace_diff_applies_with_git(tmp_path: Path):  # F9
    original = {"README.md": "# school"}  # no final newline, as in scenario 3
    current = {"README.md": "# school\n\n## Setup\n\nRun it.", "scripts/setup.sh": "#!/usr/bin/env bash\necho ok\n"}
    patch = workspace_patch(["README.md", "scripts/setup.sh"], original, current)
    assert "\\ No newline at end of file\n+# school" in patch
    assert "Run it.\n\\ No newline at end of file\ndiff --git a/scripts/setup.sh" in patch
    (tmp_path / "README.md").write_text("# school", encoding="utf-8", newline="")
    (tmp_path / "change.patch").write_text(patch, encoding="utf-8", newline="")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "apply", "change.patch"], cwd=tmp_path, check=True)
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == current["README.md"]
    assert (tmp_path / "scripts" / "setup.sh").read_text(encoding="utf-8") == current["scripts/setup.sh"]


def test_a_deleted_file_is_a_deletion_in_the_diff():
    patch = workspace_patch(["old.txt"], {"old.txt": "gone\n"}, {})
    assert patch.startswith("diff --git a/old.txt b/old.txt\ndeleted file mode 100644\n--- a/old.txt\n+++ /dev/null")


def test_prompt_sections_are_measured():
    with metered() as usage:
        text = assemble("planner", [Section("JOB", "{}"), None, Section("RULES", "- one")])
    assert text == "JOB\n{}\n\nRULES\n- one"
    assert usage.prompt_chars == {"planner.JOB": 6, "planner.RULES": 11}
    assert usage.as_dict()["promptChars"] == {"planner.JOB": 6, "planner.RULES": 11}
