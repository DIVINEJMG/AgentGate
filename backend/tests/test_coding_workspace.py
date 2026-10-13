"""Coding workspace steps: edits that are safe to repeat and never echo whole files, a local git
checkpoint per change with rollback, every change saved outside the sandbox so a lost sandbox is
rebuilt, and a real queue for workspaces. The in-sandbox helper runs here against real git."""
from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.bootstrap.settings import settings
from app.execution.coding import sandbox_helper as helper
from app.execution.coding import service
from app.execution.contracts import ExecutionError, ExecutionProviderError
from app.execution.integration_execution import execute_native_action
from app.infrastructure.database import models as m
from app.runtime.managed import _provider_wait
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes
from tests.test_approvals_phase4 import TABLES
from tests.test_efficiency_queries import ORG, Database
from tests.test_integration_foundation import Coordination, execution


def _text(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _save(path: str, content: str | bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(content.encode() if isinstance(content, str) else content)


def _clean(root: str) -> bool:
    return subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True,
                          check=True).stdout == ""


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is needed for workspace checkpoints")


@pytest.fixture
def workspace() -> str:
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8", newline="") as handle:
        handle.write("def total(a, b):\n    return a + b\n\nprint(total(1, 2))\n")
    return root


def test_replace_edits_only_the_changed_text_and_is_safe_to_repeat(workspace: str) -> None:
    request = {"path": "app.py", "mode": "replace", "replacements": [{"find": "return a + b", "replace": "return a + b + 0"}]}
    first = helper.edit(workspace, request)
    assert not first["alreadyApplied"] and first["diffStat"] == "1 file changed, 1 insertion(+), 1 deletion(-)"
    assert "content" not in first and first["size"] == len(_text(os.path.join(workspace, "app.py")))
    again = helper.edit(workspace, request)  # a retried delivery: nothing changes twice
    assert again["alreadyApplied"] and again["checkpoint"] == first["checkpoint"]


@pytest.mark.parametrize(("find", "message"), [("total", "appears 2 times"), ("missing()", "was not found")])
def test_replace_refuses_ambiguous_or_missing_text_with_a_fix(workspace: str, find: str, message: str) -> None:
    with pytest.raises(helper.StepError, match=message):
        helper.edit(workspace, {"path": "app.py", "mode": "replace", "replacements": [{"find": find, "replace": "x"}]})


def test_large_files_are_written_in_chunks_at_the_right_offset(workspace: str) -> None:
    chunk = "x" * 9000
    helper.edit(workspace, {"path": "big.txt", "mode": "append", "offset": 0, "content": chunk})
    second = helper.edit(workspace, {"path": "big.txt", "mode": "append", "offset": 9000, "content": chunk})
    assert second["size"] == 18000  # larger than one 10,000-character message
    assert helper.edit(workspace, {"path": "big.txt", "mode": "append", "offset": 9000, "content": chunk})["alreadyApplied"]
    with pytest.raises(helper.StepError, match="is 18000 characters long, not 5"):
        helper.edit(workspace, {"path": "big.txt", "mode": "append", "offset": 5, "content": "y"})


def test_patches_apply_once_and_rollback_returns_to_any_checkpoint(workspace: str) -> None:
    base = helper.init(workspace)["baseline"]
    first = helper.edit(workspace, {"path": "new.py", "mode": "write", "content": "VALUE = 1\n"})
    patch_file = os.path.join(tempfile.mkdtemp(), "change.patch")
    with open(patch_file, "w", encoding="utf-8", newline="") as handle:
        handle.write("--- a/new.py\n+++ b/new.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n")
    patched = helper.patch(workspace, patch_file)
    assert not patched["alreadyApplied"] and helper.patch(workspace, patch_file)["alreadyApplied"]
    undone = helper.rollback(workspace, first["checkpoint"])  # undo the patch only
    assert undone["undone"] and _text(os.path.join(workspace, "new.py")) == "VALUE = 1\n"
    assert helper.rollback(workspace, first["checkpoint"])["undone"] is False  # repeating changes nothing
    helper.rollback(workspace, base)
    assert not os.path.exists(os.path.join(workspace, "new.py"))
    assert _text(os.path.join(workspace, "app.py")).startswith("def total")


def test_paths_outside_the_workspace_are_refused(workspace: str) -> None:
    for path in ("../escape.py", ".git/config"):
        with pytest.raises(helper.StepError, match="outside the workspace"):
            helper.edit(workspace, {"path": path, "mode": "write", "content": "x"})


class LocalSandbox:
    """The E2B runtime interface over local folders, running the real helper script."""

    def __init__(self) -> None:
        self.roots: dict[str, str] = {}
        self.tmp = tempfile.mkdtemp()
        self.commands: list[str] = []

    def add(self, sandbox_id: str, files: dict[str, str]) -> str:
        root = self.roots[sandbox_id] = tempfile.mkdtemp()
        for path, content in files.items():
            os.makedirs(os.path.dirname(os.path.join(root, path)) or root, exist_ok=True)
            with open(os.path.join(root, path), "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
        return root

    def _local(self, sandbox_id: str, path: str) -> str:
        if path.startswith("/workspace/"):
            return os.path.join(self.roots[sandbox_id], path.removeprefix("/workspace/"))
        return os.path.join(self.tmp, os.path.basename(path))

    async def write(self, sandbox_id: str, path: str, content: str | bytes) -> None:
        local = self._local(sandbox_id, path)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        _save(local, content)

    async def read(self, sandbox_id: str, path: str) -> str:
        return _text(self._local(sandbox_id, path))

    async def run(self, sandbox_id: str, command: str) -> Any:
        self.commands.append(command)
        parts = shlex.split(command)
        if parts[0] == "rm":
            os.remove(self._local(sandbox_id, parts[-1]))
            return SimpleNamespace(exit_code=0, stdout="")
        assert parts[:2] == ["python3", service.HELPER_FILE]
        args = [self.roots[sandbox_id], parts[3], *[self._local(sandbox_id, arg) if arg.startswith("/tmp/") else arg
                                                    for arg in parts[4:]]]
        out = io.StringIO()
        with redirect_stdout(out):
            code = helper.main(args)
        return SimpleNamespace(exit_code=code, stdout=out.getvalue())


@pytest.fixture
def artifacts(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    store: dict[str, bytes] = {}

    async def save(request: Any, content: bytes, media_type: str) -> dict[str, str]:
        identity = str(uuid4())
        store[identity] = content
        return {"artifactId": identity}

    async def load(identity: str, request: Any) -> bytes:
        return store[identity]

    monkeypatch.setattr(service, "store_task_artifact", save)
    monkeypatch.setattr(service, "load_artifact", load)
    return store


@pytest.mark.asyncio
async def test_each_change_is_saved_outside_the_sandbox_and_rollback_forgets_undone_ones(artifacts: dict[str, bytes]) -> None:
    sandbox = LocalSandbox()
    sandbox.add("sb-1", {"app.py": "A = 1\n"})
    row = SimpleNamespace(sandbox_id="sb-1", evidence={})
    request = SimpleNamespace(organization_id=ORG, work_item_id=uuid4())
    await sandbox.write("sb-1", "/tmp/audoryn-step.json", json.dumps({"path": "app.py", "mode": "replace",
                                                                       "replacements": [{"find": "A = 1", "replace": "A = 2"}]}))
    first = await service.run_helper(sandbox, row, "edit", "/tmp/audoryn-step.json")
    await service.save_file_checkpoint(sandbox, row, request, first)
    await sandbox.write("sb-1", "/tmp/audoryn-step.json", json.dumps({"path": "b.py", "mode": "write", "content": "B\n"}))
    second = await service.run_helper(sandbox, row, "edit", "/tmp/audoryn-step.json")
    await service.save_file_checkpoint(sandbox, row, request, second)
    entries = row.evidence["fileCheckpoints"]
    assert [list(entry["files"]) for entry in entries] == [["app.py"], ["b.py"]]
    assert artifacts[entries[0]["files"]["app.py"]] == b"A = 2\n"
    assert sum("init" in command for command in sandbox.commands) == 1  # the helper is set up once per sandbox
    target, remaining = service.rollback_target(row.evidence, {"to": "previous"})
    assert target == first["checkpoint"] and remaining == entries[:1]
    assert service.rollback_target(row.evidence, {"to": "baseline"}) == (row.evidence["gitBaseline"], [])
    with pytest.raises(ValueError, match="not one of this workspace"):
        service.rollback_target(row.evidence, {"checkpoint": "abcdef1"})


@pytest.mark.asyncio
async def test_a_refused_edit_reaches_the_planner_as_a_clear_message() -> None:
    sandbox = LocalSandbox()
    sandbox.add("sb-1", {"app.py": "A = 1\n"})
    await sandbox.write("sb-1", "/tmp/audoryn-step.json", json.dumps({"path": "app.py", "mode": "replace",
                                                                       "replacements": [{"find": "B = 1", "replace": "B = 2"}]}))
    with pytest.raises(ValueError, match="was not found in app.py"):
        await service.run_helper(sandbox, SimpleNamespace(sandbox_id="sb-1", evidence={}), "edit", "/tmp/audoryn-step.json")


@pytest.mark.asyncio
async def test_a_lost_sandbox_is_rebuilt_with_every_saved_change(monkeypatch: pytest.MonkeyPatch,
                                                                  artifacts: dict[str, bytes]) -> None:
    db = Database(TABLES)
    sandbox = LocalSandbox()
    baseline = {"app.py": "A = 1\n", "old.py": "OLD\n"}
    sandbox.add("sb-1", baseline)
    row = db.add(m.CodingSession, organization_id=ORG, work_item_id=uuid4(), resource_id=uuid4(), sandbox_id="sb-1",
                 base_sha="a" * 40, status="running", fencing_token=1, active_seconds=0,
                 last_activity_at=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(hours=1),
                 evidence={"baselineArtifactId": "b", "initialization": {"stage": "ready", "manifestArtifactId": "m"}})
    template, _ = execution()
    request = replace(template, organization_id=ORG, work_item_id=row.work_item_id,
                      resource=replace(template.resource, id=str(row.resource_id)))
    live = SimpleNamespace(sandbox_id="sb-1", evidence=dict(row.evidence))
    for change in ({"path": "app.py", "mode": "replace", "replacements": [{"find": "A = 1", "replace": "A = 2"}]},
                   {"path": "new.py", "mode": "write", "content": "NEW\n"}, {"path": "old.py", "mode": "delete"}):
        await sandbox.write("sb-1", "/tmp/audoryn-step.json", json.dumps(change))
        result = await service.run_helper(sandbox, live, "edit", "/tmp/audoryn-step.json")
        await service.save_file_checkpoint(sandbox, live, request, result)
    with Session(db.engine) as session:
        stored = session.get(m.CodingSession, row.id)
        assert stored is not None
        stored.evidence = live.evidence
        session.commit()

    def factory() -> Any:
        return BenchSession(Session(db.engine, expire_on_commit=False))

    async def initialize(provider: Any, rebuilt_request: Any, *args: Any) -> dict[str, Any]:
        assert rebuilt_request.input == {"sha": "a" * 40}  # the same pinned commit
        sandbox.add("sb-2", baseline)  # a fresh sandbox holds only the baseline
        with Session(db.engine) as session:
            current = session.get(m.CodingSession, row.id)
            assert current is not None and current.status == "creating" and current.sandbox_id is None
            current.sandbox_id, current.status = "sb-2", "running"
            session.commit()
        return {}

    monkeypatch.setattr(service, "session_factory", factory)
    monkeypatch.setattr("app.execution.coding.initialization.initialize", initialize)
    with _aware_sqlite_datetimes():
        await service.rebuild_workspace(None, request, {}, None, sandbox)
    rebuilt = sandbox.roots["sb-2"]
    assert _text(os.path.join(rebuilt, "app.py")) == "A = 2\n"
    assert _text(os.path.join(rebuilt, "new.py")) == "NEW\n"
    assert not os.path.exists(os.path.join(rebuilt, "old.py"))
    with Session(db.engine) as session:
        current = session.get(m.CodingSession, row.id)
        assert current is not None and current.evidence["lostSandboxes"] == ["sb-1"]
        assert len(current.evidence["fileCheckpoints"]) == 1  # one restored checkpoint, rollback-ready
    assert _clean(rebuilt)


class Queue:
    def __init__(self, ahead: int) -> None:
        self.ahead, self.left = ahead, False

    async def queue_position(self, key: str, member: str, *, stale_seconds: int) -> int:
        return self.ahead

    async def queue_leave(self, key: str, member: str) -> None:
        self.left = True

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(("running", "ahead", "message"), [
    (1, 0, "this task is next"),
    (1, 2, "2 tasks ahead"),
    (0, 1, "1 task ahead"),   # a free workspace goes to the task that has waited longest
])
async def test_waiting_tasks_get_a_place_in_line(monkeypatch: pytest.MonkeyPatch, running: int, ahead: int, message: str) -> None:
    queue = Queue(ahead)
    monkeypatch.setattr(service.RedisCoordinator, "from_settings", classmethod(lambda cls: queue))
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[running, running]))
    request = SimpleNamespace(organization_id=ORG, work_item_id=uuid4(), operation="repository.workspace.open",
                              correlation_id="c")
    with pytest.raises(ExecutionProviderError) as caught:
        await service.admit_capacity(session, request)
    assert caught.value.error.code == "queued" and message in caught.value.error.safe_message
    assert not queue.left


@pytest.mark.asyncio
async def test_the_first_in_line_is_admitted_and_leaves_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = Queue(0)
    monkeypatch.setattr(service.RedisCoordinator, "from_settings", classmethod(lambda cls: queue))
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[0, 0]))
    await service.admit_capacity(session, SimpleNamespace(organization_id=ORG, work_item_id=uuid4()))
    assert queue.left


def test_waiting_in_the_queue_is_bounded_by_time_not_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ExecutionError(code="queued", retryable=True, provider="github", operation="o", correlation_id="c",
                           safe_message="Waiting for a coding workspace", retry_after_seconds=60)
    fresh = SimpleNamespace(payload={"runtime": {"providerRetryCount": 2, "providerWaitCount": 99}})
    delay, counter = _provider_wait(cast(Any, fresh), error, retry_limit=2, backoff_seconds=5) or (0, {})
    assert delay == 60 and "providerQueuedSince" in counter
    long_ago = (datetime.now(UTC) - timedelta(seconds=settings.coding_queue_max_wait_seconds + 1)).isoformat()
    assert _provider_wait(cast(Any, SimpleNamespace(payload={"runtime": {"providerQueuedSince": long_ago}})), error,
                          retry_limit=2, backoff_seconds=5) is None


@pytest.mark.asyncio
async def test_a_workspace_wait_does_not_cool_down_the_whole_account() -> None:
    request, context = execution(False)
    request = replace(request, capability=replace(request.capability, target="workspace"))
    coordinator = Coordination()

    class Busy:
        async def execute(self, **kw: Any) -> Any:
            raise ExecutionProviderError(code="rate_limited", retryable=True, provider="github", operation="o",
                                         correlation_id="c", safe_message="Another workspace operation is in progress.",
                                         retry_after_seconds=60)

    with pytest.raises(ExecutionProviderError):
        await execute_native_action(provider=Busy(), request=request, context=context, coordinator=cast(Any, coordinator))
    assert not getattr(coordinator, "cache", {})
