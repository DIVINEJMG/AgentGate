"""Development fixture planner: supplied decisions, observed outputs, real validation."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from app.runtime.planner.adaptive import AdaptiveRuntimePlanner


class FixtureBlocked(RuntimeError):
    pass


class ScriptedPlanner(AdaptiveRuntimePlanner):
    def __init__(self, manifest: Path, resource_id: str, work_item_id: str, publish: bool):
        self.plan = json.loads(manifest.read_text(encoding="utf-8"))
        self.files = {path: (manifest.parent / source).read_text(encoding="utf-8")
                      for path, source in self.plan["files"].items()}
        self.fingerprint = hashlib.sha256(json.dumps(
            {"plan": self.plan, "files": self.files, "resourceId": resource_id,
             "workItemId": work_item_id, "publish": publish}, sort_keys=True,
        ).encode()).hexdigest()
        self.resource_id = resource_id
        self.branch = f"codex/{work_item_id[:8]}/scripted-timezone-check"
        self.publish = publish
        self.verification_recovery_step: int | None = None
        super().__init__(cast(Any, None))  # Validation only; no gateway calls are made.
        self.validator = self

    def evidence(self, observations):
        result = {}
        for row in observations:
            title = row.get("title", "")
            if not title.startswith("[script:") or row.get("status", "completed") != "completed":
                continue
            key = title.split("]", 1)[0][8:]
            data = row.get("providerOutput", {})
            if isinstance(data, dict) and "evidenceLimits" in data:
                if data["evidenceLimits"].get("truncated"):
                    raise FixtureBlocked("Required evidence was truncated; inspect the saved artifact before continuing.")
                data = data.get("data", {})
            result[key] = data
        return result

    def resolve(self, value, evidence):
        if isinstance(value, dict):
            if "$observed" in value:
                parts = value["$observed"].split(".")
                found = evidence
                for key in parts:
                    if not isinstance(found, dict) or key not in found:
                        raise FixtureBlocked(f"Missing observed field: {value['$observed']}")
                    found = found[key]
                return found
            if "$file" in value:
                return self.files[value["$file"]]
            if "$branch" in value:
                return self.branch
            if "$command" in value:
                return self.plan["command"]
            return {key: self.resolve(nested, evidence) for key, nested in value.items()}
        if isinstance(value, list):
            return [self.resolve(nested, evidence) for nested in value]
        return value

    async def choose_next(self, *, tools, observations, **kwargs):
        evidence = self.evidence(observations)
        if evidence.get("metadata", {}).get("fullName", self.plan["repository"]).lower() != self.plan["repository"].lower():
            raise FixtureBlocked("Observed repository does not match the supplied manifest.")
        test = evidence.get("test_status", {})
        verification_steps = [row.get("step") for row in observations
            if str(row.get("title", "")).startswith("[script:test_status]")
            and row.get("status") == "completed"]
        refresh_verification = (test.get("sourceVerificationAvailable") is False
            and self.verification_recovery_step is not None and bool(verification_steps)
            and all(isinstance(step, int) and step <= self.verification_recovery_step for step in verification_steps))
        if test.get("status") in {"uncertain_outcome", "cancelled"}:
            raise FixtureBlocked("Test outcome is uncertain or cancelled; command replay is not authorized.")
        if test.get("status") == "completed" and test.get("exitCode") != 0:
            raise FixtureBlocked("Focused tests did not pass. Saved output must be inspected before publication.")
        if test.get("sourceVerificationAvailable") is False and not refresh_verification:
            raise FixtureBlocked("Completed tests lack verified source evidence. Inspect the saved verification failure; the command will not replay.")
        for step in self.plan["steps"]:
            key = step["id"]
            if key in evidence and not (key == "test_status" and (test.get("status") != "completed"
                    or refresh_verification
                    or "sourceVerificationAvailable" not in test and "sourceUnchangedDuringCommand" not in test)):
                continue
            if key == "task_branch":
                diff = evidence["diff"]
                if not diff.get("publicationAllowed"):
                    raise FixtureBlocked("Workspace export did not authorize publication.")
                paths = {change["path"] for change in diff.get("changes", [])}
                if not paths or not paths.issubset(self.files):
                    raise FixtureBlocked("Diff is empty or contains files outside the supplied fixture.")
                if test.get("sourceUnchangedDuringCommand") is not True:
                    raise FixtureBlocked("The tested source revision is not proven unchanged.")
                if test.get("sourceFingerprint") != diff.get("sourceFingerprint"):
                    raise FixtureBlocked("The diff differs from the tested source.")
                if not self.publish:
                    return self.finish(observations, tools, "Inspection, edits, focused tests and diff finished. Publication is disabled.", attention=True)
            if key.startswith("edit_"):
                inspected = evidence["inspect_script" if key == "edit_script" else "inspect_tests"]
                content = inspected.get("content")
                if inspected.get("encoding") == "base64" and isinstance(content, str):
                    content = base64.b64decode(content).decode("utf-8")
                missing = inspected.get("exists") is False and inspected.get("repositoryAccessible") is True and inspected.get("revisionAccessible") is True
                if not missing and content != self.files[step["input"]["path"]]:
                    raise FixtureBlocked("An existing target differs from the fixture. Review it and update the fixture explicitly; automatic replacement is disabled.")
            raw = {"decision": "act", "summary": f"Scripted fixture: {key}",
                   "title": f"[script:{key}] {step['operation']}",
                   "instruction": "Execute only this supplied typed action and record its actual outcome.",
                   "resourceId": self.resource_id, "scope": "github." + step["operation"],
                   "input": self.resolve(step["input"], evidence)}
            try:
                return self.validator._validate(raw, tools=tools, observations=observations, latest_browser=None)
            except RuntimeError as error:
                raise FixtureBlocked(str(error)) from error
        pr = evidence["verify_pr"]
        sha = evidence["published"]["commit"]["sha"]
        if not pr.get("draft") or pr.get("state") != "open" or pr.get("head", {}).get("sha") != sha:
            raise FixtureBlocked("The reread PR is not an open draft at the observed published commit.")
        return self.finish(observations, tools,
            f"Verified draft PR: {pr['html_url']}; commit: {sha}. "
            f"Command: {self.plan['command']}; exit: {test['exitCode']}. "
            f"Actual output: {test.get('stdout', '')} {test.get('stderr', '')}. PR left unmerged.")

    def finish(self, observations, tools, summary, attention=False):
        return self.validator._validate({"decision": "finish", "summary": summary,
            "resultStatus": "attention" if attention else "completed"},
            tools=tools, observations=observations, latest_browser=None)

    def tools(self, provider):
        return [{"scope": action.scope, "resourceId": self.resource_id,
                 "inputSchema": action.descriptor().input_schema}
                for action in provider.actions.values()]
