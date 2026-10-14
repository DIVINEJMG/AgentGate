"""The runs and approvals lists load their records in a fixed number of queries (efficiency pass).

The workspace reloads these lists on live events, so per-row queries multiplied database transfer.
Each test keeps the previous per-row code as the reference and checks that:
- the batched code returns exactly the same records;
- the number of queries does not grow with the number of rows;
- step input and output (large JSON) are never read for the runs list.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.governance_routes import _approvals_public
from app.api.runtime_result_routes import _runs_public
from app.application.services.approval_conversation import approval_conversation_id
from app.infrastructure.database import models as m


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


TABLES = [m.Worker, m.Job, m.JobRevision, m.WorkItem, m.Run, m.RunStep, m.Artifact, m.AgentIdentity, m.Action, m.Approval]
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
ORG = uuid4()
BIG = {"actionEvidence": {"outcome": "succeeded"}, "screenshot": "x" * 20_000}


def _filler(column: Any) -> Any:
    kind = column.type
    if isinstance(kind, JSONB):
        return {}
    if isinstance(kind, DateTime):
        return NOW
    if isinstance(kind, Boolean):
        return False
    if isinstance(kind, Integer):
        return 0
    if isinstance(kind, String | Text):
        return "x" + uuid4().hex[:12]
    if column.type.python_type is UUID:
        return uuid4()
    raise AssertionError(f"no filler for {column}")


class AsyncAdapter:
    """The few AsyncSession methods the routes use, over a synchronous SQLite session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        return self._session.get(*args, **kwargs)

    async def scalar(self, *args: Any, **kwargs: Any) -> Any:
        return self._session.scalar(*args, **kwargs)

    async def scalars(self, *args: Any, **kwargs: Any) -> Any:
        return self._session.scalars(*args, **kwargs)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._session.execute(*args, **kwargs)


class Database:
    def __init__(self, tables: list[Any] | None = None) -> None:
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        for model in tables or TABLES:
            model.__table__.create(self.engine)
        self.statements: list[str] = []
        event.listen(self.engine, "before_cursor_execute", lambda conn, cursor, statement, *rest: self.statements.append(statement))
        self.session = Session(self.engine)

    def add(self, model: Any, /, **values: Any) -> Any:
        row: dict[str, Any] = {}
        for column in model.__table__.columns:
            if column.name in values:
                row[column.name] = values[column.name]
            elif getattr(column.default, "is_callable", False):
                row[column.name] = uuid4() if column.name == "id" else column.default.arg(None)
            elif getattr(column.default, "is_scalar", False):
                row[column.name] = column.default.arg
            elif not column.nullable and column.server_default is None:
                row[column.name] = _filler(column)
        with self.engine.begin() as connection:
            connection.execute(model.__table__.insert(), row)
        return self.session.get(model, row["id"])

    def count(self, model: Any, rows: list[Any], function: Any) -> tuple[Any, int]:
        """Runs ``function(session, rows)`` in a fresh session; counts only its own queries.

        The listed rows are loaded first (the endpoint has them from its own list query), so
        what is counted is the work of turning them into public records.
        """
        session = Session(self.engine)
        fresh = [session.get(model, row.id) for row in rows]
        self.statements.clear()
        result = run(function(AsyncAdapter(session), fresh))
        return result, len(self.statements)


def seed(db: Database, runs: int) -> dict[str, Any]:
    agent = db.add(m.AgentIdentity, organization_id=ORG, name="Release agent")
    worker = db.add(m.Worker, organization_id=ORG, agent_identity_id=agent.id)
    job = db.add(m.Job, organization_id=ORG, worker_id=worker.id)
    revision = db.add(m.JobRevision, job_id=job.id, definition={"autonomy": {"resultsThreadId": "thread-from-autonomy"}, "large": "y" * 5_000})
    created: list[Any] = []
    approvals: list[Any] = []
    for index in range(runs):
        origin = {"integrationOrigin": {"threadId": f"thread-{index}"}} if index % 2 else {}
        item = db.add(m.WorkItem, organization_id=ORG, job_id=job.id, job_revision_id=revision.id,
                      payload={**origin, "runtime": {"attempt": index + 1, "currentStep": index, "planSummary": f"plan {index}",
                                                     "queueState": "retrying" if index == 0 else "running", "providerRetryAt": "2026-10-12T13:00:00Z",
                                                     "contextMemoryIds": ["m1"]}, "bulk": "z" * 5_000})
        run = db.add(m.Run, organization_id=ORG, work_item_id=item.id, status="running", correlation_id=f"c-{index}",
                     result_summary=None, created_at=NOW + timedelta(minutes=index), updated_at=NOW + timedelta(minutes=index))
        for step_index in (2, 0, 1):
            db.add(m.RunStep, run_id=run.id, step_index=step_index, kind="action", status="completed",
                   input={"title": "t", "instruction": "i" * 2_000}, output={"data": {"output": BIG}})
        for _ in range(index % 3):
            db.add(m.Artifact, organization_id=ORG, run_id=run.id, storage_key=f"k-{uuid4()}", media_type="image/png")
        action = db.add(m.Action, organization_id=ORG, agent_id=agent.id, run_id=run.id, status="pending_approval",
                        resource_id="repo", scope="write", idempotency_key=f"idem-{index}",
                        payload={"request": {"provider": "github", "action": "merge", "input": {"n": index}},
                                 "policy": {"outcome": "REQUIRE_APPROVAL"}, "result": {"summary": "done"} if index % 2 else None})
        approvals.append(db.add(m.Approval, organization_id=ORG, action_id=action.id,
                                status="approved" if index % 2 else "pending",
                                decided_by=uuid4() if index % 2 else None, decision_reason="ok" if index % 2 else None))
        created.append(run)
    # A run whose work item is gone, and an action with no run: both must still render.
    created.append(db.add(m.Run, organization_id=ORG, work_item_id=uuid4(), status="queued", correlation_id="orphan"))
    lone = db.add(m.Action, organization_id=ORG, agent_id=agent.id, run_id=None, status="pending_approval",
                  resource_id="repo", scope="read", idempotency_key="idem-lone", payload={})
    approvals.append(db.add(m.Approval, organization_id=ORG, action_id=lone.id, status="pending"))
    return {"runs": created, "approvals": approvals}


# ---------- the previous per-row code, kept as the reference ----------

async def legacy_run_public(session: Any, run: Any) -> dict[str, Any]:
    from app.api.runtime_result_routes import _run_record

    item = await session.get(m.WorkItem, run.work_item_id)
    job = await session.get(m.Job, item.job_id) if item else None
    worker = await session.get(m.Worker, job.worker_id) if job else None
    steps = list((await session.scalars(select(m.RunStep).where(m.RunStep.run_id == run.id).order_by(m.RunStep.step_index))).all())
    artifacts = list((await session.scalars(select(m.Artifact).where(m.Artifact.run_id == run.id))).all())
    raw_runtime = item.payload.get("runtime") if item is not None and isinstance(item.payload, dict) else None
    return _run_record(
        run, dict(raw_runtime) if isinstance(raw_runtime, dict) else {},
        job_id=str(job.id) if job else "", worker_id=str(worker.id) if worker else "",
        agent_id=str(worker.agent_identity_id) if worker else "",
        step_ids=[str(step.id) for step in steps], artifact_ids=[str(artifact.id) for artifact in artifacts],
    )


async def legacy_approval_public(session: Any, approval: Any) -> dict[str, Any]:
    from app.api.governance_routes import _action_record, _approval_record

    action = await session.get(m.Action, approval.action_id)
    agent = await session.get(m.AgentIdentity, action.agent_id)
    action_public = _action_record(action, agent.name if agent else "", None)
    return _approval_record(approval, action, action_public, await approval_conversation_id(session, action))


def run(coroutine: Any) -> Any:
    import asyncio
    return asyncio.run(coroutine)


# ---------- tests ----------

@pytest.mark.parametrize("size", [3, 12])
def test_the_runs_list_is_unchanged_and_takes_five_queries_whatever_its_size(size: int) -> None:
    db = Database()
    rows = seed(db, size)["runs"]
    expected, legacy_queries = db.count(m.Run, rows, lambda session, fresh: _all(legacy_run_public, session, fresh))
    actual, queries = db.count(m.Run, rows, _runs_public)
    assert actual == expected
    assert queries == 5
    assert legacy_queries >= 4 * len(rows)
    # Ordered step IDs, retry time and missing links behave as before.
    assert expected[0]["retryAt"] == "2026-10-12T13:00:00Z"
    assert expected[-1]["jobId"] == "" and expected[-1]["stepIds"] == []


def test_the_runs_list_never_reads_step_input_or_output() -> None:
    db = Database()
    rows = seed(db, 4)["runs"]
    db.count(m.Run, rows, _runs_public)
    steps = [statement for statement in db.statements if "run_steps" in statement]
    assert steps and all("run_steps.output" not in statement and "run_steps.input" not in statement for statement in steps)
    # The work item payload is only read through a JSON path (its "runtime" part), never whole.
    assert all(re.search(r"(?<!JSON_EXTRACT\()work_items\.payload", statement) is None for statement in db.statements)


@pytest.mark.parametrize("size", [3, 12])
def test_the_approvals_list_is_unchanged_and_takes_a_fixed_number_of_queries(size: int) -> None:
    db = Database()
    rows = seed(db, size)["approvals"]
    expected, legacy_queries = db.count(m.Approval, rows, lambda session, fresh: _all(legacy_approval_public, session, fresh))
    actual, queries = db.count(m.Approval, rows, _approvals_public)
    assert actual == expected
    assert queries <= 5
    assert legacy_queries >= 4 * size
    threads = {item["conversationThreadId"] for item in expected}
    assert {"thread-1", "thread-from-autonomy", None} <= threads


def test_the_new_queries_are_valid_postgresql() -> None:
    db = Database()
    seeded = seed(db, 2)
    session = AsyncAdapter(db.session)
    compiled: list[str] = []
    original = db.session.execute

    def compiling(statement: Any, *args: Any, **kwargs: Any) -> Any:
        compiled.append(str(statement.compile(dialect=postgresql.dialect())))
        return original(statement, *args, **kwargs)

    db.session.execute = compiling  # type: ignore[method-assign]
    run(_runs_public(cast(Any, session), seeded["runs"]))
    run(_approvals_public(cast(Any, session), seeded["approvals"]))
    assert any("payload[" in statement for statement in compiled)
    assert any("definition[" in statement for statement in compiled)


async def _all(function: Any, session: Any, rows: list[Any]) -> list[dict[str, Any]]:
    return [await function(session, row) for row in rows]


def test_reference_helpers_match_the_live_single_record_paths() -> None:
    # The detail endpoints now go through the batched code with one record.
    from app.api.governance_routes import _approval_public
    from app.api.runtime_result_routes import _run_public

    db = Database()
    seeded = seed(db, 2)
    session = AsyncAdapter(db.session)
    assert run(_run_public(cast(Any, session), seeded["runs"][1])) == run(legacy_run_public(session, seeded["runs"][1]))
    assert run(_approval_public(cast(Any, session), seeded["approvals"][1])) == run(legacy_approval_public(session, seeded["approvals"][1]))
