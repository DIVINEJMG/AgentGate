"""Real JSON dirty tracking plus fenced, transaction-free workspace I/O."""
import json
from contextlib import asynccontextmanager, nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.application.services.integration_foundation import IntegrationFoundation
from app.execution.coding import service
from app.execution.contracts import ExecutionProviderError
from app.execution.providers.native.github.actions.coding import ACTIONS


@pytest.mark.parametrize("succeeds", [True, False])
async def test_command_verification_survives_real_json_reload(monkeypatch, succeeds):
    class Base(DeclarativeBase):
        pass

    class Command(Base):
        __tablename__ = "command_evidence"
        id: Mapped[str] = mapped_column(String, primary_key=True)
        status: Mapped[str] = mapped_column(String)
        output: Mapped[dict] = mapped_column(JSON)
        session_id: UUID
        organization_id: UUID
        process_id: int
        command: str
        __allow_unmapped__ = True

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    identity = str(uuid4())
    with Session(engine, expire_on_commit=False) as database:
        command = Command(id=identity, status="running", output={"startFingerprint": "tested"})
        command.session_id, command.organization_id = uuid4(), uuid4()
        command.process_id, command.command = 1, "tests"
        database.add(command)
        database.commit()

        async def commit():
            database.commit()

        session = SimpleNamespace(get=AsyncMock(return_value=command), commit=commit)
        request = SimpleNamespace(input={"commandId": identity}, organization_id=command.organization_id)
        runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(stdout="tests passed")),
                                  command_result=AsyncMock(return_value={"exitCode": 0}))
        export = AsyncMock(return_value={"sourceFingerprint": "tested", "publicationAllowed": True, "publicationBlocker": None})
        if not succeeds:
            export.side_effect = ConnectionError("secret must not be logged")
        monkeypatch.setattr(service, "export", export)
        await service.inspect_command(session, runtime, SimpleNamespace(id=command.session_id, sandbox_id="sandbox"), request, False, {})
        with Session(engine) as reader:
            saved = reader.get(Command, identity)
            assert saved is not None
            assert saved.status == "completed"
            assert saved.output["exitCode"] == 0
            assert saved.output["sourceVerificationAvailable"] is succeeds
            assert saved.output["sourceUnchangedDuringCommand"] is succeeds
    engine.dispose()


async def test_legacy_completed_command_is_verified_without_relaunch(monkeypatch):
    command = SimpleNamespace(id=uuid4(), session_id=uuid4(), organization_id=uuid4(),
        status="completed", command="tests", output={"exitCode": 0, "startFingerprint": "tested"})
    session = SimpleNamespace(get=AsyncMock(return_value=command), commit=AsyncMock())
    runtime = SimpleNamespace(run=AsyncMock(), command_result=AsyncMock())
    monkeypatch.setattr(service, "export", AsyncMock(return_value={"sourceFingerprint": "tested", "publicationAllowed": True, "publicationBlocker": None}))
    result = await service.inspect_command(session, runtime, SimpleNamespace(id=command.session_id),
        SimpleNamespace(input={"commandId": str(command.id)}, organization_id=command.organization_id), False, {})
    assert result["sourceUnchangedDuringCommand"] is True
    runtime.run.assert_not_awaited()
    runtime.command_result.assert_not_awaited()


@pytest.fixture
def workspace(monkeypatch):
    row = SimpleNamespace(id=uuid4(), sandbox_id="sandbox", status="running", fencing_token=0,
        active_seconds=0, active_since=None, last_activity_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=1), base_sha="a" * 40,
        evidence={"budgetDay": datetime.now(UTC).date().isoformat()})
    request = SimpleNamespace(operation="repository.workspace.diff", work_item_id=uuid4(), organization_id=uuid4(),
        agent_id=uuid4(), resource=SimpleNamespace(id=str(uuid4())), input={},
        capability=SimpleNamespace(scope="github.repository.workspace.diff"), correlation_id="fixture")

    class Database:
        no_autoflush = nullcontext()
        transaction = False
        tracked = None
        saved = deepcopy(row)
        item = SimpleNamespace(status="running")

        async def scalar(self, _):
            self.transaction = True
            self.tracked = deepcopy(self.saved)
            return self.tracked

        async def get(self, *_args, **_kwargs):
            self.transaction = True
            return self.item

        async def commit(self):
            if self.tracked:
                self.saved = deepcopy(self.tracked)
            self.transaction = False

        def expunge(self, _):
            self.tracked = None

    database = Database()

    @asynccontextmanager
    async def factory():
        yield database

    async def connect(*_):
        assert not database.transaction

    runtime = SimpleNamespace(connect=connect, release=AsyncMock())
    coordinator = SimpleNamespace(acquire_lock=AsyncMock(return_value="lease"), renew_lock=AsyncMock(return_value=True),
        release_lock=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(service, "session_factory", factory)
    monkeypatch.setattr(service.RedisCoordinator, "from_settings", lambda: coordinator)
    monkeypatch.setattr(IntegrationFoundation, "authorize", AsyncMock())
    return request, database, runtime, coordinator


async def test_diff_io_has_no_transaction_and_accepts_owned_evidence(workspace, monkeypatch):
    request, database, runtime, _ = workspace

    async def export(_runtime, row, *_):
        assert not database.transaction
        row.evidence = {**row.evidence, "exportArtifactId": "saved-diff"}
        return {"changes": []}

    monkeypatch.setattr(service, "export", export)
    result = await service.execute_coding_action(None, request, {}, None, runtime)
    assert result["changes"] == []
    assert database.saved.evidence["exportArtifactId"] == "saved-diff"


@pytest.mark.parametrize("change", ["generation", "cancelled", "lease", "authority"])
async def test_late_diff_cannot_overwrite_changed_ownership(workspace, monkeypatch, change):
    request, database, runtime, coordinator = workspace

    async def export(*_):
        assert not database.transaction
        if change == "generation":
            database.saved.fencing_token += 1
        elif change == "cancelled":
            database.item.status = "cancelled"
        elif change == "lease":
            coordinator.renew_lock.return_value = False
        else:
            monkeypatch.setattr(IntegrationFoundation, "authorize", AsyncMock(side_effect=PermissionError("revoked")))
        return {"changes": []}

    monkeypatch.setattr(service, "export", export)
    with pytest.raises((ExecutionProviderError, PermissionError)):
        await service.execute_coding_action(None, request, {}, None, runtime)
    assert "exportArtifactId" not in database.saved.evidence


async def test_diff_failure_retries_inspection_and_logs_no_secret(workspace, monkeypatch, caplog):
    request, database, runtime, _ = workspace

    async def export(*_):
        assert not database.transaction
        service._stage.set("saved_diff_read")
        raise ConnectionError("Bearer SECRET")

    monkeypatch.setattr(service, "export", export)
    with pytest.raises(ExecutionProviderError) as failure:
        await service.execute_coding_action(None, request, {}, None, runtime)
    assert failure.value.error.code == "temporary_provider_error"
    assert failure.value.error.retryable
    assert "stage=saved_diff_read" in caplog.text
    assert "error_type=ConnectionError" in caplog.text
    assert "SECRET" not in caplog.text


async def test_full_diff_export_releases_transaction_for_every_external_call(workspace, monkeypatch):
    request, database, runtime, _ = workspace
    database.saved.evidence.update(baselineArtifactId="baseline", fingerprintAlgorithm="manifest-sha256-v1")

    async def load(*_):
        assert not database.transaction
        return b"{}"

    async def write(*_):
        assert not database.transaction

    async def run(*_):
        assert not database.transaction
        return SimpleNamespace(exit_code=0, stdout=json.dumps({"manifest": {}, "changed": {}}))

    async def store(*_):
        assert not database.transaction
        return {"artifactId": "new-diff"}

    runtime.write, runtime.run = write, run
    monkeypatch.setattr(service, "load_artifact", load)
    monkeypatch.setattr(service, "store_task_artifact", store)
    result = await service.execute_coding_action(None, request, {}, None, runtime)
    assert result["artifact"]["artifactId"] == "new-diff"
    assert database.saved.evidence["exportArtifactId"] == "new-diff"


async def test_lost_edit_acknowledgement_remains_uncertain(workspace):
    request, database, runtime, _ = workspace
    request.operation, request.capability.scope = "repository.workspace.edit", "github.repository.workspace.edit"
    request.input = {"path": "allowed.py", "content": "saved edit"}
    runtime.run = AsyncMock(return_value=SimpleNamespace(exit_code=0))

    async def write(*_):
        assert not database.transaction
        raise ConnectionError("lost acknowledgement")

    runtime.write = write
    runtime.read = AsyncMock()
    with pytest.raises(ExecutionProviderError) as failure:
        await service.execute_coding_action(None, request, {}, None, runtime)
    assert failure.value.error.code == "uncertain_outcome"
    assert not failure.value.error.retryable
    runtime.read.assert_not_awaited()


def test_workspace_effects_are_explicit_and_api_permissions_unchanged():
    actions = {a.operation: a for a in ACTIONS}
    for operation in ("edit", "patch", "command.start", "command.cancel", "close"):
        action = actions["repository.workspace." + operation]
        assert action.descriptor().side_effect
        assert action.descriptor().mode == "action"
        assert action.permission == "contents:read"
        assert action.method == "GET"  # Local effect is independent of HTTP transport.
    for operation in ("diff", "read", "search", "command.status"):
        assert not actions["repository.workspace." + operation].descriptor().side_effect
    # Initialization has its own durable creation/bundle reconciliation path.
    assert actions["repository.workspace.open"].execution_semantics == "initialization"
