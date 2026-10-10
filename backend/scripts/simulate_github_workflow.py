"""Offline-first fixture execution. Live mode replaces only one process's planner."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.domain.actions.gateway import ActionGateway, ActionProposal, AuthorizationDecision
from app.domain.identity.principals import AgentPrincipal
from app.domain.integrations.contracts import IntegrationExecutionResult
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.native.github.safety import (
    safe_path,
    validate_changes,
    validate_task_branch,
)
from scripts.github_scripted_planner import FixtureBlocked, ScriptedPlanner


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(8):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            # Windows scanners can briefly hold a newly written checkpoint open.
            if attempt == 7:
                raise
            time.sleep(0.05)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@contextmanager
def offline_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "runner.lock").open("a+b") as handle:
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise FixtureBlocked("Another offline runner owns this state directory") from error
        yield


class UncertainFixtureWrite(RuntimeError):
    pass


class FixtureGuard:
    def __init__(self, adapter, approve):
        self.adapter, self.approve = adapter, approve

    async def evaluate(self, *, principal, proposal):
        if proposal.resource_id != self.adapter.planner.resource_id:
            return AuthorizationDecision("DENY", "Fixture resource mismatch")
        if self.adapter.state["scenario"] == "approval" and proposal.operation == "repository.branch.create" and not self.approve:
            return AuthorizationDecision("REQUIRE_APPROVAL", "Offline fixture approval is required")
        return AuthorizationDecision("ALLOW", "Exact offline fixture authority")


class OfflineAdapter:
    """Fake external state; actual local file changes/tests, schema and safety checks."""
    def __init__(self, planner, state, directory):
        self.planner, self.state, self.directory = planner, state, directory
        self.provider = ExpandedGitHubProvider()
        self.workspace = directory / "workspace"
        self.external_path = directory / "fake-provider.json"
        self.external = json.loads(self.external_path.read_text()) if self.external_path.exists() else {
            "ledger": {}, "branches": {"main": "a" * 40}, "prs": {}, "writes": 0,
        }

    def files(self):
        return {path: (self.workspace / path).read_text(encoding="utf-8")
                for path in self.planner.files if (self.workspace / path).exists()}

    async def execute(self, proposal):
        values = await self.provider.normalize_input(operation=proposal.operation, input=proposal.payload)
        identity = fingerprint({"operation": proposal.operation, "input": values})
        ledger = self.external["ledger"].get(proposal.idempotency_key)
        if ledger:
            if ledger["fingerprint"] != identity:
                raise FixtureBlocked("An action identity was reused with changed content")
            return IntegrationExecutionResult(proposal.operation, "Offline exact-identity reconciliation", ledger["data"])
        output = self.perform(proposal.operation, values)
        self.external["ledger"][proposal.idempotency_key] = {"fingerprint": identity, "data": output}
        save(self.external_path, self.external)
        if self.state["scenario"] == "uncertain-write" and proposal.operation == "repository.commit.create":
            raise UncertainFixtureWrite("Fake commit succeeded but its response was lost; resume reconciles the exact action identity.")
        return IntegrationExecutionResult(proposal.operation, "Offline fixture observation", output)

    def perform(self, operation, value):
        if operation == "repository.metadata.read":
            return {"fullName": self.planner.plan["repository"], "defaultBranch": "main"}
        if operation == "repository.branch.read":
            return {"name": value["branch"], "commit": {"sha": self.external["branches"][value["branch"]]}}
        if operation == "repository.contents.read":
            return {"exists": False, "repositoryAccessible": True, "revisionAccessible": True,
                    "path": value["path"], "revision": value["ref"]}
        if operation == "repository.workspace.open":
            self.workspace.mkdir(parents=True, exist_ok=True)
            return {"baseSha": value["sha"], "status": "running", "sessionId": "offline-workspace"}
        if operation == "repository.workspace.edit":
            validate_changes([value], {"allowedPaths": list(self.planner.files)})
            destination = self.workspace / safe_path(value["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(value["content"], encoding="utf-8")
            return {"path": value["path"]}
        if operation == "repository.workspace.command.start":
            # Only the supplied stdlib test command is allowed. No shell or backend secrets.
            expected = "cd /workspace/backend && python -B -m unittest discover -s tests -p test_check_timezone_data.py -v"
            if value["command"] != expected:
                raise FixtureBlocked("Offline adapter permits only the documented fixture test command")
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}}
            before = fingerprint(self.files())
            result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
                "-p", "test_check_timezone_data.py", "-v"], cwd=self.workspace / "backend",
                env=env, capture_output=True, text=True, timeout=60, check=False)
            command = {"commandId": "offline-command", "command": expected, "status": "completed",
                       "exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
                       "sourceFingerprint": fingerprint(self.files()),
                       "sourceUnchangedDuringCommand": before == fingerprint(self.files())}
            self.external["command"] = command
            return {"commandId": command["commandId"], "status": "running"}
        if operation == "repository.workspace.command.status":
            if value["commandId"] != "offline-command":
                raise FixtureBlocked("Unknown fixture command")
            return self.external["command"]
        if operation == "repository.workspace.diff":
            current = self.files()
            changes = [{"path": path, "content": content} for path, content in current.items()]
            validate_changes(changes, {"allowedPaths": list(self.planner.files)})
            patch = "".join("".join(difflib.unified_diff([], content.splitlines(keepends=True),
                fromfile="a/" + path, tofile="b/" + path)) for path, content in current.items())
            return {"changes": changes, "diff": patch, "sourceFingerprint": fingerprint(current),
                    "publicationAllowed": True, "baseSha": "a" * 40}
        if operation == "repository.branch.create":
            validate_task_branch(value["branch"], "main", self.state["workItemId"])
            self.external["branches"][value["branch"]] = value["sha"]
            self.external["writes"] += 1
            return {"ref": "refs/heads/" + value["branch"], "object": {"sha": value["sha"]}}
        if operation == "repository.commit.create":
            if self.state["scenario"] == "branch-race":
                self.external["branches"][value["branch"]] = "b" * 40
                save(self.external_path, self.external)
            if self.external["branches"][value["branch"]] != value["baseSha"]:
                raise FixtureBlocked("The branch moved; publication paused without force-push")
            validate_task_branch(value["branch"], "main", self.state["workItemId"])
            validate_changes(value["changes"], {"allowedPaths": list(self.planner.files)})
            sha = hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()
            self.external["branches"][value["branch"]] = sha
            self.external["writes"] += 1
            return {"object": {"sha": sha}}
        if operation == "repository.pull_request.create":
            pr = {"number": 1, "draft": True, "state": "open", "merged": False,
                  "html_url": "https://example.invalid/offline/pull/1",
                  "head": {"sha": self.external["branches"][value["head"]]}}
            self.external["prs"]["1"] = pr
            self.external["writes"] += 1
            return pr
        if operation == "repository.pull_request.read":
            return self.external["prs"][str(value["number"])]
        raise FixtureBlocked("The offline adapter has no implementation for " + operation)


async def offline(args):
    directory = args.state_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = directory / "checkpoint.json"
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {
        "workItemId": str(uuid4()), "resourceId": str(uuid4()), "organizationId": str(uuid4()),
        "agentId": str(uuid4()), "observations": [], "scenario": args.scenario,
    }
    if state["scenario"] != args.scenario:
        raise FixtureBlocked("Resume with the original scenario or use a new state directory")
    planner = ScriptedPlanner(args.manifest, state["resourceId"], state["workItemId"], True)
    if state.get("fixtureFingerprint", planner.fingerprint) != planner.fingerprint:
        raise FixtureBlocked("Fixture changed; use a new state directory instead of replaying old authority")
    state["fixtureFingerprint"] = planner.fingerprint
    save(checkpoint, state)
    adapter = OfflineAdapter(planner, state, directory)
    tools = planner.tools(adapter.provider)
    principal = AgentPrincipal(UUID(state["agentId"]), UUID(state["organizationId"]), "offline",
        frozenset(tool["scope"] for tool in tools), "low", ())
    gateway = ActionGateway((FixtureGuard(adapter, args.approve_fixture),), adapter)
    for _ in range(args.max_steps):
        try:
            decision = await planner.choose_next(tools=tools, observations=state["observations"])
        except FixtureBlocked as error:
            state.update(status="attention", reason=str(error))
            save(checkpoint, state)
            print(str(error))
            return 2
        if decision.decision == "finish":
            state.update(status="completed", summary="OFFLINE SIMULATION: " + decision.summary, simulated=True,
                         fakeProviderWrites=adapter.external["writes"])
            save(checkpoint, state)
            print(json.dumps({"mode": "offline", "status": state["status"], "summary": state["summary"],
                              "fakeProviderWrites": state["fakeProviderWrites"]}, indent=2))
            return 0
        key = decision.title.split("]", 1)[0][8:]
        proposal = ActionProposal(UUID(state["organizationId"]), UUID(state["agentId"]), "github",
            decision.scope.removeprefix("github."), decision.scope, planner.resource_id,
            decision.action_input, "offline-script", state["workItemId"] + ":" + key)
        state.update(status="pending", pendingAction=key)
        save(checkpoint, state)
        print(f"Offline executing: {key}", flush=True)
        try:
            result = await gateway.execute(principal=principal, proposal=proposal)
        except (FixtureBlocked, PermissionError, UncertainFixtureWrite) as error:
            state.update(status="attention", reason=str(error))
            save(checkpoint, state)
            print(str(error))
            return 2
        state["observations"].append({"title": decision.title, "scope": decision.scope,
            "resourceId": planner.resource_id, "status": "completed", "providerOutput": result.data,
            "verification": {"verified": True, "summary": "Offline fixture only"}})
        state.pop("pendingAction", None)
        save(checkpoint, state)
        if args.stop_after and len(state["observations"]) >= args.stop_after:
            print("Stopped at a saved checkpoint. Resume with the same state directory.")
            return 2
    raise FixtureBlocked("Script step budget exhausted; saved observations remain available")


def saved_retry_delay(item, now):
    retry_at = ((item.payload or {}).get("runtime") or {}).get("providerRetryAt")
    if item.status != "queued" or not retry_at:
        return 0.0
    return max(0.0, (datetime.fromisoformat(retry_at) - now).total_seconds())


async def live(args):
    if not args.work_item_id or not args.resource_id or not args.organization_id or not args.allow_coding or not args.workers_stopped:
        raise FixtureBlocked("Live mode requires exact organization/work-item/resource IDs, --allow-coding and --workers-stopped")
    diagnostic_logger = logging.getLogger("uvicorn.error")
    if not diagnostic_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        diagnostic_logger.addHandler(handler)
        diagnostic_logger.propagate = False
    diagnostic_logger.setLevel(logging.INFO)
    from app.bootstrap.settings import settings
    from app.infrastructure.database import session as database
    from app.infrastructure.database.models import IntegrationResource, WorkItem
    from app.infrastructure.redis.coordination import RedisCoordinator
    from app.runtime.managed import ManagedRuntimeExecutor

    # Local process only: no inference, no normal routing/configuration changes.
    settings.smart_planner_enabled = False
    database.request_outbox_drain_after_commit = lambda **kwargs: None
    planner = ScriptedPlanner(args.manifest, args.resource_id, args.work_item_id, args.publish)
    async with database.session_factory() as session:
        item = await session.get(WorkItem, UUID(args.work_item_id))
        resource = await session.get(IntegrationResource, UUID(args.resource_id))
        org = UUID(args.organization_id)
        if not item or not resource or item.organization_id != org or resource.organization_id != org:
            raise FixtureBlocked("Work item/resource not found in the exact organization")
        if resource.provider != "github" or resource.configuration.get("repository", "").lower() != planner.plan["repository"].lower():
            raise FixtureBlocked("Resource does not identify the supplied GitHub repository")
        recovering = bool(getattr(args, "resume_inspection", False))
        if item.status not in {"queued", "running", "waiting_approval"} and not (recovering and item.status in {"uncertain_outcome", "partial_completion"}):
            raise FixtureBlocked(f"Work item is {item.status}; use its normal approval/recovery controls first")
        previous = (item.payload or {}).get("scriptedPlannerFixture")
        if recovering and previous != planner.fingerprint:
            raise FixtureBlocked("Recovery requires the exact previously saved scripted fixture.")
        if previous and previous != planner.fingerprint:
            raise FixtureBlocked("A different fixture already owns this work item")
        runtime = ManagedRuntimeExecutor(session)
        if recovering and item.status in {"uncertain_outcome", "partial_completion"}:
            # Verify the entire saved action history before releasing the paused task.
            from sqlalchemy import select

            from app.infrastructure.database.models import Run
            from scripts.github_inspection_recovery import resume_inspection
            saved_run = await session.scalar(select(Run).where(Run.organization_id == org,
                Run.work_item_id == item.id).order_by(Run.created_at.desc()).limit(1))
            if not saved_run:
                raise FixtureBlocked("Recovery requires the existing run.")
            history = await runtime._load_steps(saved_run)
            if any(step.kind == "action" and not str((step.input or {}).get("title", "")).startswith("[script:") for step in history):
                raise FixtureBlocked("Recovery refuses unscripted action history.")
            coordinator = RedisCoordinator.from_settings()
            try:
                recovered_step = await resume_inspection(session, item, runtime, args.resource_id, coordinator)
            finally:
                await coordinator.close()
            print(json.dumps({"mode": "live", "status": "inspection_recovery_started",
                "workItemId": str(item.id), "step": recovered_step, "inferenceCalls": 0}), flush=True)
        run = await runtime._ensure_run(item)
        steps = await runtime._load_steps(run)
        if any(step.kind == "action" and not str((step.input or {}).get("title", "")).startswith("[script:") for step in steps):
            raise FixtureBlocked("This work item contains unscripted actions; use a fresh authorized work item")
        item.payload = {**(item.payload or {}), "scriptedPlannerFixture": planner.fingerprint}
        await session.commit()
        planner.verification_recovery_step = ((item.payload or {}).get("runtime") or {}).get("verificationRecoveryStep")
        runtime._planner = planner
        for _ in range(args.max_steps):
            await session.refresh(item)
            retry_at = ((item.payload or {}).get("runtime") or {}).get("providerRetryAt")
            if item.status == "queued" and retry_at:
                delay = saved_retry_delay(item, datetime.now(UTC))
                if delay:
                    print(json.dumps({"mode": "live", "status": "waiting_retry", "retryAt": retry_at,
                        "workItemId": str(item.id)}), flush=True)
                    await session.commit()  # No database transaction while waiting.
                    await asyncio.sleep(delay)
                    await session.refresh(item)
            current = int(((item.payload or {}).get("runtime") or {}).get("currentStep", 0))
            coordinator = RedisCoordinator.from_settings()
            lease = await coordinator.acquire_lock(f"runtime:{item.id}:{current}",
                ttl_seconds=int(settings.runtime_planner_timeout_seconds) + 120)
            if lease is None:
                await coordinator.close()
                raise FixtureBlocked("Another runtime owns this step; nothing was dispatched")
            try:
                outcome = await runtime.execute_step(item=item, expected_step=current)
            except FixtureBlocked as error:
                # Report through the real completion/conversation path, truthfully as attention.
                job, _revision, worker, _agent = await runtime._load_context(item)
                run = await runtime._ensure_run(item)
                steps = await runtime._load_steps(run)
                outcome = await runtime._complete(item, run, job, worker, steps,
                    completion_summary="Scripted verification stopped: " + str(error) + " Completed steps remain saved.",
                    result_status="attention")
            finally:
                await coordinator.release_lock(lease)
                await coordinator.close()
            await session.commit()
            print(json.dumps({"mode": "live", "workItemId": str(item.id), "status": item.status,
                "step": outcome.current_step, "state": outcome.state, "summary": outcome.summary}), flush=True)
            if outcome.state != "continue":
                return 0 if item.status == "completed" else 2
            if args.stop_after and outcome.current_step >= args.stop_after:
                print("Stopped at a saved runtime checkpoint; keep workers stopped and resume this script.")
                return 2
            await asyncio.sleep(args.poll_seconds)
    raise FixtureBlocked("Script step budget exhausted; backend checkpoints remain saved")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "prepare", "live"], default="offline")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).parent / "fixtures/github_workflow/plan.json")
    parser.add_argument("--state-dir", type=Path, default=Path(".local/github-simulation"))
    parser.add_argument("--scenario", choices=["missing-files", "branch-race", "uncertain-write", "approval"], default="missing-files")
    parser.add_argument("--stop-after", type=int)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--approve-fixture", action="store_true", help="Offline fixture only; never approves live actions")
    parser.add_argument("--work-item-id")
    parser.add_argument("--source-work-item-id", help="Existing authorized work item used only as preparation lineage")
    parser.add_argument("--preparation-key", help="Stable key for idempotent preparation; change only for a new test")
    parser.add_argument("--resource-id")
    parser.add_argument("--organization-id")
    parser.add_argument("--allow-coding", action="store_true", help="Authorize actual E2B workspace usage")
    parser.add_argument("--workers-stopped", action="store_true", help="Confirm normal backend/runtime workers are stopped")
    parser.add_argument("--resume-inspection", action="store_true", help="Explicitly resume an uncertain or partially completed scripted inspection; never replays mutations")
    parser.add_argument("--publish", action="store_true", help="Authorize real task-branch, commit and draft PR writes")
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()
    if args.max_steps < 1 or args.poll_seconds < 1:
        parser.error("Use a positive step budget and at least one second between live steps")
    try:
        if args.mode == "prepare":
            from scripts.github_workflow_preparation import prepare
            print(json.dumps(asyncio.run(prepare(args)), indent=2))
            return 0
        if args.mode == "live":
            return asyncio.run(live(args))
        with offline_lock(args.state_dir):
            return asyncio.run(offline(args))
    except FixtureBlocked as error:
        print("Needs attention: " + str(error), file=sys.stderr)
        return 2
    except PermissionError as error:
        print("Needs attention: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
