import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.application.services.preparation_ai import (
    PreparationObserver,
    restart_exhausted_preparation,
)
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIProviderError
from app.infrastructure.database.models import PlannerAttempt, PlannerDecision


def state(status="exhausted", expired=False):
    command = SimpleNamespace(id=uuid4(), organization_id=uuid4(), target_id=None,
        status="accepted", payload={"integrationTask": {"instruction": "Fix authorized files"}})
    old = PlannerDecision(id=uuid4(), organization_id=command.organization_id,
        command_id=command.id, step_index=0, status=status, generation=2, recovery_round=1,
        purpose="integration_preparation", deadline=datetime.now(UTC) + timedelta(seconds=-1 if expired else 100))
    added = []
    session = SimpleNamespace(scalar=AsyncMock(return_value=old), flush=AsyncMock(),
        add=lambda row: added.append(row), refresh=AsyncMock(), execute=AsyncMock())
    return session, command, old, added


@pytest.mark.asyncio
@pytest.mark.parametrize("expired,status", [(False, "exhausted"), (True, "pending")])
async def test_explicit_retry_creates_fresh_budget_and_keeps_old_attempts(monkeypatch, expired, status):
    monkeypatch.setattr(settings, "smart_planner_enabled", True)
    monkeypatch.setattr(settings, "runtime_planner_timeout_seconds", 2000)
    session, command, old, added = state(status, expired)
    attempt = PlannerAttempt(id=uuid4(), decision_id=old.id, status="failed", error_category="quota_exhausted")
    before = datetime.now(UTC)
    assert await restart_exhausted_preparation(session, command) == "fresh_decision_queued"
    fresh = added[0]
    assert fresh.id != old.id and fresh.step_index == old.step_index + 1
    assert 1999 <= (fresh.deadline - before).total_seconds() <= 2001
    assert fresh.status == "pending" and fresh.recovery_round == 0
    assert old.status == "superseded" and old.generation == 3
    assert attempt.decision_id == old.id and attempt.error_category == "quota_exhausted"
    assert command.payload["activePreparationDecisionId"] == str(fresh.id)
    session.scalar.return_value = fresh
    assert await restart_exhausted_preparation(session, command) == "queued"
    assert len(added) == 1  # Duplicate resume does not extend the active budget.


@pytest.mark.asyncio
async def test_prepared_work_cannot_be_restarted(monkeypatch):
    monkeypatch.setattr(settings, "smart_planner_enabled", True)
    session, command, _, added = state()
    command.target_id = str(uuid4())
    assert await restart_exhausted_preparation(session, command) == "already_prepared"
    session.scalar.assert_not_awaited()
    assert not added


@pytest.mark.asyncio
async def test_old_response_is_rejected_after_human_retry(monkeypatch):
    monkeypatch.setattr(settings, "smart_planner_enabled", True)
    session, command, old, _ = state()
    observer = PreparationObserver(session, old, command,
        SimpleNamespace(renew_lock=AsyncMock(return_value=True)), object())
    await restart_exhausted_preparation(session, command)
    with pytest.raises(AIProviderError) as error:
        await observer.accept({"ok": True})
    assert error.value.category == "cancelled"
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_quota_diagnostics_are_persisted_with_scope():
    session, command, old, _ = state()
    session.commit = AsyncMock()
    observer = PreparationObserver(session, old, command, None, None)
    observer.attempt = PlannerAttempt(id=uuid4(), status="started")
    await observer.reject(AIProviderError("quota_exhausted", "local budget", retryable=False,
        organization_scoped=True, budget_scope="organization_tokens", model_called=False))
    assert observer.attempt.failure_details["budgetScope"] == "organization_tokens"
    assert observer.attempt.failure_details["organizationScoped"]
    assert observer.attempt.failure_details["modelCalled"] is False


def test_audit_migration_upgrade_and_downgrade_sql():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0014_preparation_retry.py"
    spec = importlib.util.spec_from_file_location("retry_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        module.upgrade()
        module.downgrade()
    sql = output.getvalue()
    assert "ADD COLUMN failure_details JSONB" in sql
    assert "DROP COLUMN failure_details" in sql
    assert "DELETE" not in sql


@pytest.mark.asyncio
async def test_fresh_decision_replaces_local_old_task_without_losing_new_owner(monkeypatch):
    from app.runtime import preparation_worker as worker

    monkeypatch.setattr(worker, "_tasks", {})
    monkeypatch.setattr(worker, "_decision_ids", {})

    async def prepare(command_id):
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_prepare", prepare)
    command_id = uuid4()
    worker.start_preparation(command_id)
    old = worker._tasks[command_id]
    await asyncio.sleep(0)
    worker.start_preparation(command_id, str(uuid4()))
    fresh = worker._tasks[command_id]
    with pytest.raises(asyncio.CancelledError):
        await old
    await asyncio.sleep(0)
    assert worker._tasks[command_id] is fresh
    await worker.stop_preparations()
