"""Real bundle/helper execution with deterministic fake external services."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import subprocess
import sys
import tarfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.execution.coding import initialization, service
from app.execution.coding.bundles import (
    BUNDLE_BYTES,
    INITIALIZE_HELPER,
    build_bundles,
    source_manifest,
)
from app.execution.coding.e2b import E2BCodingRuntime
from app.execution.coding.manifest_export import SCAN_SCRIPT
from app.execution.contracts import ExecutionProviderError


def snapshot(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for path, value in files.items():
            raw = value.encode()
            info = tarfile.TarInfo("repo/" + path)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return out.getvalue()


class FakeSession:
    def __init__(self, item):
        self.row = None
        self.item = item
        self.commits = 0
        self.transaction = False

    async def scalar(self, _):
        self.transaction = True
        return self.row

    def add(self, row):
        self.row = row

    async def get(self, *_args, **_kwargs):
        self.transaction = True
        return self.item

    async def refresh(self, *_args, **_kwargs):
        self.transaction = True

    async def commit(self):
        self.commits += 1
        self.transaction = False


def local_helper_source(content: str, directory: str, root: str) -> str:
    directory = directory.replace("\\", "/")

    def relocate(match: re.Match[str]) -> str:
        path = match.group(1)
        return repr(root if path == "/workspace" else directory + "/" + path.removeprefix("/tmp/"))

    # One substitution pass prevents remapping an inserted Linux /tmp path.
    return re.sub(r"'(/workspace|/tmp/[^']*)'", relocate, content)


@pytest.mark.parametrize("directory", [
    "/tmp/pytest-of-runner/pytest-0/example",
    "C:\\Users\\divin\\AppData\\Local\\Temp\\example",
    "/tmp/operator's-tests",
])
def test_local_helper_paths_are_relocated_once(directory):
    source = (
        "root='/workspace'\n"
        "manifest='/tmp/audoryn-source-manifest.json'\n"
        "archive='/tmp/audoryn-source-'+str(0)+'.tar'\n"
        "import json\nprint(json.dumps(dict(root=root, manifest=manifest, archive=archive)))\n"
    )
    expected = directory.replace("\\", "/")
    outcome = subprocess.run(
        [sys.executable, "-c", local_helper_source(source, directory, expected + "/workspace")],
        capture_output=True, text=True, check=True,
    )
    values = json.loads(outcome.stdout)
    assert values["root"] == expected + "/workspace"
    assert values["manifest"] == expected + "/audoryn-source-manifest.json"
    assert values["archive"] == expected + "/audoryn-source-0.tar"


class LocalRuntime:
    def __init__(self, directory, session):
        self.directory = directory
        self.session = session
        self.creates = self.transfers = 0
        self.fail_bundle = False
        self.files = {}

    async def create(self, **_kwargs):
        assert not self.session.transaction
        self.creates += 1
        return "sandbox"

    async def find(self, _):
        assert not self.session.transaction
        return "sandbox" if self.creates else None

    async def connect(self, *_):
        assert not self.session.transaction

    async def set_timeout(self, *_):
        assert not self.session.transaction

    async def write(self, _, path, content):
        assert not self.session.transaction
        if path.endswith(".tar"):
            self.transfers += 1
        self.files[path] = content
        if path.endswith(".tar") and self.fail_bundle:
            self.fail_bundle = False
            raise ConnectionError("lost upload acknowledgement")

    async def run(self, _, command):
        assert not self.session.transaction
        root = self.directory / "workspace"
        root.mkdir(exist_ok=True)
        if command == "mkdir -p /workspace":
            return SimpleNamespace(exit_code=0, stdout="")
        for path, content in self.files.items():
            destination = self.directory / path.removeprefix("/tmp/")
            if path.endswith(".py"):
                content = local_helper_source(content, str(self.directory), str(root))
            destination.write_bytes(content.encode() if isinstance(content, str) else content)
        args = command.split()[2:]
        outcome = await asyncio.to_thread(subprocess.run,
            [sys.executable, str(self.directory / "audoryn-init-helper.py"), *args],
            capture_output=True, text=True, check=False)
        return SimpleNamespace(exit_code=outcome.returncode, stdout=outcome.stdout, stderr=outcome.stderr)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    item = SimpleNamespace(id=uuid4(), status="running")
    session = FakeSession(item)
    artifacts = {}
    files = {f"src/file-{i}.txt": "hello " * 500 for i in range(80)}
    provider = SimpleNamespace(repo_path=lambda _: "/repos/owner/repo", api=AsyncMock(return_value=SimpleNamespace(raw=snapshot(files))))
    request = SimpleNamespace(organization_id=uuid4(), work_item_id=item.id, agent_id=uuid4(),
        resource=SimpleNamespace(id=str(uuid4())), input={"sha": "a" * 40},
        capability=SimpleNamespace(scope="github.repository.workspace.open"),
        operation="repository.workspace.open", correlation_id="test")

    @asynccontextmanager
    async def factory():
        yield session

    async def store(_request, content, _media):
        assert not session.transaction
        identity = str(uuid4())
        artifacts[identity] = content
        return {"artifactId": identity}

    async def load(identity, _request):
        assert not session.transaction
        return artifacts[identity]

    coordinator = SimpleNamespace(acquire_lock=AsyncMock(return_value="lease"),
        renew_lock=AsyncMock(return_value=True), release_lock=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(initialization, "session_factory", factory)
    monkeypatch.setattr(initialization.RedisCoordinator, "from_settings", lambda: coordinator)
    monkeypatch.setattr(initialization, "IntegrationFoundation", lambda _: SimpleNamespace(authorize=AsyncMock()))
    monkeypatch.setattr(service, "reserve", AsyncMock(return_value="today"))
    monkeypatch.setattr(service, "store_task_artifact", store)
    monkeypatch.setattr(service, "load_artifact", load)
    runtime = LocalRuntime(tmp_path, session)
    return SimpleNamespace(session=session, artifacts=artifacts, files=files, runtime=runtime,
        provider=provider, request=request, coordinator=coordinator)


@pytest.mark.asyncio
async def test_initialization_bundles_many_files_without_transactions_during_io(harness):
    h = harness
    output = await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    assert output["status"] == "running"
    assert output["sourceFiles"] == 80
    assert h.runtime.creates == 1 and h.runtime.transfers == 1
    assert h.provider.api.await_count == 2
    assert h.session.row.evidence["initialization"]["stage"] == "ready"
    assert h.session.commits < len(h.files)
    await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    assert h.runtime.creates == 1 and h.runtime.transfers == 1


@pytest.mark.asyncio
async def test_interrupted_upload_resumes_same_deadline_and_sandbox(harness):
    h = harness
    h.runtime.fail_bundle = True
    with pytest.raises(ConnectionError):
        await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    deadline = h.session.row.evidence["initialization"]["deadline"]
    await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    assert h.runtime.creates == 1
    assert h.runtime.transfers == 1
    assert h.provider.api.await_count == 2
    assert h.session.row.evidence["initialization"]["deadline"] == deadline


@pytest.mark.asyncio
async def test_expired_initialization_does_not_create_or_reset_budget(harness):
    h = harness
    h.runtime.fail_bundle = True
    with pytest.raises(ConnectionError):
        await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    init = h.session.row.evidence["initialization"]
    init["deadline"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ExecutionProviderError) as caught:
        await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    assert not caught.value.error.retryable
    assert h.runtime.creates == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["cancelled", "lease_lost", "permission_changed"])
async def test_initialization_respects_current_ownership_and_authority(harness, monkeypatch, blocker):
    h = harness
    if blocker == "cancelled":
        h.session.item.status = "cancelled"
    elif blocker == "lease_lost":
        h.coordinator.renew_lock.return_value = False
    else:
        monkeypatch.setattr(initialization, "IntegrationFoundation", lambda _: SimpleNamespace(
            authorize=AsyncMock(side_effect=PermissionError("revoked"))))
    with pytest.raises((ExecutionProviderError, PermissionError)):
        await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    assert h.runtime.creates == 0


def test_bundle_packaging_is_deterministic_and_bounded():
    files = {str(i): "x" * 1_000_000 for i in range(7)}
    bundles = build_bundles(files)
    assert len(bundles) == 2
    assert bundles == build_bundles(files)
    assert all(sum(len(files[p]) for p in paths) <= BUNDLE_BYTES for _, paths in bundles)


@pytest.mark.asyncio
async def test_e2b_connection_is_reused_and_action_handles_released(monkeypatch):
    from pydantic import SecretStr

    from app.bootstrap.settings import settings
    monkeypatch.setattr(settings, "coding_execution_enabled", True)
    monkeypatch.setattr(settings, "e2b_api_key", SecretStr("fixture"))
    monkeypatch.setattr(settings, "coding_template_id", "fixture")
    sandbox = SimpleNamespace(files=SimpleNamespace(write=AsyncMock()), set_timeout=AsyncMock())
    sdk = SimpleNamespace(connect=AsyncMock(return_value=sandbox))
    runtime = E2BCodingRuntime(sdk)
    for _ in range(10):
        await runtime.write("sandbox", "/tmp/fixture", "content")
    assert sdk.connect.await_count == 1
    await runtime.release()
    assert not runtime._sandboxes


def test_manifest_scan_transfers_only_changed_files_and_preserves_binary(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    baseline = {"same.txt": "x" * 100000, "edit.txt": "before", "delete.txt": "old",
                "binary.dat": {"binaryBase64": base64.b64encode(b"\xff\x00").decode()}}
    (root / "same.txt").write_text(baseline["same.txt"])
    (root / "edit.txt").write_text("after")
    (root / "binary.dat").write_bytes(b"\xff\x00")
    (root / ".pytest_cache").mkdir()
    (root / ".pytest_cache/generated").write_text("cache")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(source_manifest(baseline)))
    code = SCAN_SCRIPT.replace("'/workspace'", repr(str(root))).replace("'/tmp/audoryn-baseline-manifest.json'", repr(str(path)))
    outcome = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    result = json.loads(outcome.stdout)
    assert result["changed"] == {"edit.txt": "after"}
    assert "delete.txt" not in result["manifest"]
    assert "binary.dat" in result["manifest"]
    assert len(outcome.stdout) < 1000


def test_helper_independently_rejects_unsafe_paths(tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"files": {"../escape": {"size": 1, "sha256": "bad"}}, "bundles": []}))
    code = INITIALIZE_HELPER.replace("'/workspace'", repr(str(tmp_path / "workspace"))).replace("'/tmp/audoryn-source-manifest.json'", repr(str(spec)))
    outcome = subprocess.run([sys.executable, "-c", code, "verify-all"], capture_output=True, text=True, check=False)
    assert outcome.returncode != 0


@pytest.mark.asyncio
async def test_lost_sandbox_creation_response_is_reconciled_not_repeated(harness):
    h = harness
    original = h.runtime.create

    async def lost_response(**kwargs):
        await original(**kwargs)
        raise ConnectionError("response lost")

    h.runtime.create = lost_response
    with pytest.raises(ConnectionError):
        await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    h.runtime.create = original
    await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    assert h.runtime.creates == 1


@pytest.mark.asyncio
async def test_ready_workspace_never_restores_changed_files(harness):
    h = harness
    await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    path = h.runtime.directory / "workspace/src/file-0.txt"
    path.write_text("user edit")
    await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    assert path.read_text() == "user edit"
    assert h.runtime.transfers == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["verification", "late_generation"])
async def test_partial_initialization_resumes_or_rejects_stale_acceptance(harness, stage):
    h = harness
    original = h.runtime.run
    once = True

    async def interrupted(sandbox, command):
        nonlocal once
        result = await original(sandbox, command)
        if "verify-all" in command and once:
            once = False
            if stage == "late_generation":
                h.session.row.fencing_token += 1
            else:
                raise ConnectionError("verification response lost")
        return result

    h.runtime.run = interrupted
    with pytest.raises((ConnectionError, ExecutionProviderError)):
        await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    assert h.session.row.status != "running"
    await initialization.initialize(h.provider, h.request, {}, "credential", h.runtime)
    assert h.runtime.transfers == 1 and h.runtime.creates == 1
