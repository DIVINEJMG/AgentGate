import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.conversation_routes import task_activity
from app.application.services.task_activity import (
    activity_for,
    activity_item_query,
    activity_step_query,
    conversation_activity,
    observe_execution_ownership,
    safe_text,
)
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.models import RunStep


def test_poll_queries_project_evidence_without_file_bodies_or_objectives():
    statement = activity_step_query([uuid4()])
    sql = str(statement.compile(dialect=postgresql.dialect()))
    columns = list(statement.selected_columns)
    assert columns[-2].name == "input" and columns[-1].name == "output"
    assert "jsonb_path_query_array" in sql
    assert "$.changes[*].path" in statement.compile(dialect=postgresql.dialect()).params.values()
    # Neither entire JSON column is selected; paths/verification stay available.
    assert not columns[-1].element.compare(RunStep.output.expression)
    item = activity_item_query(uuid4(), uuid4())
    assert "jsonb_strip_nulls" in str(item.compile(dialect=postgresql.dialect()))
    assert "objective" not in item.compile(dialect=postgresql.dialect()).params.values()


def test_activity_projection_uses_asyncpg_jsonpath_and_actual_json_null():
    compiled = activity_step_query([uuid4()]).compile(dialect=asyncpg.dialect())
    sql = str(compiled)
    assert "AS JSONPATH" in sql
    assert "'null'::jsonb" in sql
    # A Python string passed as JSONB becomes JSON string "null", not JSON null.
    assert "null" not in compiled.params.values()


def state(status="running", meta=None):
    now = datetime.now(UTC)
    item = SimpleNamespace(
        id=uuid4(),
        status=status,
        created_at=now - timedelta(seconds=60),
        updated_at=now - timedelta(seconds=5),
        payload={"runtime": meta or {}},
    )
    return item, now


@pytest.mark.parametrize(
    "status,phase",
    [
        ("completed", "completed"),
        ("failed", "attention"),
        ("partial_completion", "attention"),
        ("waiting_ai", "recovery"),
        ("waiting_approval", "recovery"),
    ],
)
def test_terminal_and_wait_states_remain_visible(status, phase):
    item, now = state(status)
    step = SimpleNamespace(
        id=uuid4(),
        step_index=0,
        kind="action",
        status="completed",
        input={"scope": "github.repository.metadata.read"},
        output={},
        updated_at=item.updated_at,
    )
    activity = activity_for(item, None, [step], now)
    assert activity["phase"] == phase
    assert activity["preservedWork"] and activity["completedActions"] == 1


def test_silent_first_model_is_reported_as_waiting_not_working_on_repository():
    item, now = state(meta={"plannerPhase": "awaiting_ai", "queueState": "planning"})
    result = activity_for(item, None, [], now)
    assert result["phase"] == "awaiting_planner" and result["responseState"] == "awaiting"
    assert result["completedActions"] == 0
    assert "Reading" not in result["detail"]


def test_deadline_message_preserves_saved_work():
    item, now = state(
        "waiting_ai",
        {"lastAIErrorCategory": "timeout", "plannerExhaustion": {"reason": "deadline"}},
    )
    assert "deadline expired" in activity_for(item, None, [], now)["detail"]


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_abc123",
        "Bearer abc123",
        "api_key=abc123",
        '{"password": "abc123"}',
        "postgresql+asyncpg://user:abc123@host/db",
        "postgresql://user:abc123@host/db",
        "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----",  # fake key fixture
    ],
)
def test_command_previews_redact_credentials(secret):
    assert "abc123" not in safe_text(secret)
    assert len(safe_text("x" * 5000)) == 2000


@pytest.mark.asyncio
@pytest.mark.parametrize("access", ["allowed", "revoked", "other_tenant", "no_jobs_read"])
@pytest.mark.parametrize("projected", [False, True, "null_output", "null_changes"])
async def test_activity_reads_exact_origin_and_only_publishes_selected_evidence(monkeypatch, access, projected):
    monkeypatch.setattr(settings, "coding_execution_enabled", False)
    monkeypatch.setattr(settings, "smart_planner_enabled", False)
    org, thread = uuid4(), uuid4()
    resource_id = uuid4()
    item, _ = state()
    original_message = uuid4()
    item.payload["integrationOrigin"] = {"threadId": str(thread), "messageId": str(original_message)}
    run = SimpleNamespace(id=uuid4(), work_item_id=item.id, created_at=item.created_at)
    step = SimpleNamespace(
        id=uuid4(),
        run_id=run.id,
        step_index=0,
        kind="action",
        status="completed",
        input={"scope": "github.repository.workspace.export", "resourceId": str(resource_id)},
        updated_at=item.updated_at,
        output={
            "data": {
                "output": {"changes": [{"path": "script.py", "content": "PRIVATE_FILE_CONTENT"}]},
                "externalReferences": [{"url": "https://github.com/a/b/pull/1"}],
                "verification": {"verified": True},
                "executionCertainty": "verified",
            }
        },
    )
    if projected is True:
        step.output["data"]["output"]["changes"] = ["script.py"]
    elif projected == "null_output":
        step.output["data"]["output"] = None
    elif projected == "null_changes":
        step.output["data"]["output"]["changes"] = None
    from app.application.services.integration_foundation import IntegrationFoundation

    monkeypatch.setattr(IntegrationFoundation, "can_use", AsyncMock(return_value=access != "revoked"))
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(organization_id=uuid4() if access == "other_tenant" else org, connection_id=uuid4())),
        scalars=AsyncMock(
            side_effect=[
                SimpleNamespace(all=lambda rows=rows: rows) for rows in ([run],)
            ]
        ),
        execute=AsyncMock(side_effect=[
            SimpleNamespace(mappings=lambda rows=rows: SimpleNamespace(all=lambda: [vars(row) for row in rows]))
            for rows in ([item], [step], [])
        ]),
    )
    result = await conversation_activity(
        session, org, thread, SimpleNamespace(permissions=set() if access == "no_jobs_read" else {"jobs.read"}, user_id=uuid4())
    )
    task = result["tasks"][0]
    assert task["requestMessageId"] == str(original_message)
    assert task["changedFiles"] == (
        ["script.py"] if access == "allowed" and projected in (False, True) else []
    )
    if access == "allowed":
        assert task["links"][0]["title"] == "Open verified object"
    else:
        assert task["links"] == []
    assert "PRIVATE_FILE_CONTENT" not in json.dumps(result)
    query = session.execute.await_args_list[0].args[0].compile(dialect=postgresql.dialect())
    assert org in query.params.values() and str(thread) in query.params.values()
    assert (
        "integrationOrigin" in query.params.values()
        and "conversationOrigin" in query.params.values()
    )


@pytest.mark.asyncio
async def test_activity_endpoint_denies_cross_tenant_thread():
    org = uuid4()
    principal = SimpleNamespace(organization_id=org, permissions={"workforce.read"})
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(organization_id=uuid4())))
    with pytest.raises(HTTPException) as error:
        await task_activity(org, uuid4(), cast(HumanPrincipal, principal), cast(AsyncSession, session))
    assert error.value.status_code == 404


@pytest.fixture(autouse=True)
def activity_ownership(monkeypatch):
    from app.infrastructure.redis.coordination import RedisCoordinator
    coordinator = SimpleNamespace(active_locks=AsyncMock(side_effect=lambda keys: {key: True for key in keys}), close=AsyncMock())
    monkeypatch.setattr(RedisCoordinator, "from_settings", lambda: coordinator)
    return coordinator


def test_terminal_run_overrides_leftover_running_work_item():
    item, now = state()
    run = SimpleNamespace(status="failed", created_at=item.created_at)
    activity = activity_for(item, run, [], now)
    assert activity["status"] == "failed" and activity["phase"] == "attention"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,active,execution", [
    ("running", True, "running"),
    ("owner_gone", False, "inactive"),
    ("expired", False, "inactive"),
    ("exhausted", False, "inactive"),
    ("approval", False, "inactive"),
    ("retry", True, "retry_scheduled"),
    ("stale_retry", False, "inactive"),
    ("redis_outage", False, "unknown"),
])
async def test_live_requires_ownership_or_current_scheduled_retry(activity_ownership, scenario, active, execution):
    item, now = state()
    task = activity_for(item, None, [], now)
    task.update(_activityLease="runtime:example:0", deadlineAt=(now + timedelta(seconds=2000)).isoformat())
    if scenario != "running":
        activity_ownership.active_locks.side_effect = None
        activity_ownership.active_locks.return_value = {"runtime:example:0": False}
    if scenario == "expired":
        task["deadlineAt"] = (now - timedelta(seconds=1)).isoformat()
    if scenario == "exhausted":
        task["_activityStopped"] = True
    if scenario == "approval":
        task["status"] = "waiting_approval"
        activity_ownership.active_locks.return_value = {"runtime:example:0": True}
    if scenario in {"retry", "stale_retry"}:
        task.update(status="waiting_ai", phase="recovery", retryScheduled=True,
            retryAt=(now + timedelta(seconds=15 if scenario == "retry" else -15)).isoformat())
    if scenario == "redis_outage":
        activity_ownership.active_locks.side_effect = ConnectionError("offline")
    await observe_execution_ownership([task], now)
    assert task["isActive"] is active and task["executionState"] == execution
    assert "_activityLease" not in task and "_activityStopped" not in task
    if not active:
        assert task["elapsedSeconds"] == 55


@pytest.mark.asyncio
async def test_ownership_read_is_batched_and_never_returns_lease_tokens():
    from app.infrastructure.redis.coordination import RedisCoordinator
    client = SimpleNamespace(mget=AsyncMock(return_value=["private-owner-token", None]))
    coordinator = RedisCoordinator(cast(Redis, client))
    result = await coordinator.active_locks(["runtime:a:0", "preparation:b:c"])
    assert result == {"runtime:a:0": True, "preparation:b:c": False}
    client.mget.assert_awaited_once_with(["lock:runtime:a:0", "lock:preparation:b:c"])
    assert "private-owner-token" not in json.dumps(result)
