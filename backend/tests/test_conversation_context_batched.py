"""Improvement plan, phase 3a: the chat context is read in a fixed number of queries with exactly
the same content. `legacy_conversation_context.py` keeps the previous per-row assembler."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.application.services.conversation_context import ConversationContextAssembler
from app.infrastructure.database import models as m
from app.infrastructure.database.base import Base
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes
from tests.legacy_conversation_context import ConversationContextAssembler as LegacyAssembler
from tests.test_efficiency_queries import NOW, ORG, Database

TABLES = [mapper.class_ for mapper in Base.registry.mappers]


def seed(db: Database, size: int, *, scoped: bool) -> dict[str, Any]:
    human = db.add(m.HumanIdentity, display_name="Owner")
    role = db.add(m.WorkforceRole, organization_id=ORG, name="Analyst")
    agent = db.add(m.AgentIdentity, organization_id=ORG, name="Agent", status="active")
    worker = db.add(m.Worker, organization_id=ORG, agent_identity_id=agent.id, name="Ada", role_id=role.id,
                    supervisor_user_id=human.id, status="active",
                    profile={"description": "d" * 3_000, "responsibilities": ["r"] * 30, "instructions": "i"})
    thread = db.add(m.ConversationThread, organization_id=ORG, worker_id=worker.id if scoped else None,
                    title="T", status="active", created_by=human.id)
    for index in range(size):
        job = db.add(m.Job, organization_id=ORG, worker_id=worker.id, name=f"job {index}", status="active",
                     current_revision=2, updated_at=NOW + timedelta(minutes=index))
        for revision in (1, 2):
            definition: dict[str, Any] = {"objective": f"o{revision}" * 900, "instructions": "x" * 2_500,
                                          "completionCriteria": ["c"] * 20, "requiredCapabilities": ["s"] * 50,
                                          "autonomy": {"createdByAI": True, "secret": "never shown",
                                                       "resultsThreadId": str(thread.id)},
                                          "bulk": "z" * 10_000}
            if index % 3 == 0:
                definition["triggerConfig"] = {"id": f"cfg{index}", "schedule": {
                    "enabled": True, "cadence": "weekly", "weekdays": ["monday"] * 9, "nextDueAt": NOW.isoformat()}}
            if index % 4 == 1:
                definition.pop("completionCriteria")
            db.add(m.JobRevision, job_id=job.id, revision=revision, definition=definition, created_by=human.id)
        item = db.add(m.WorkItem, organization_id=ORG, job_id=job.id, job_revision_id=uuid4(), status="running",
                      created_at=NOW + timedelta(minutes=index),
                      payload={"integrationOrigin": {"threadId": str(thread.id)}} if index % 2 else {})
        run = db.add(m.Run, organization_id=ORG, work_item_id=item.id, status="completed",
                     result_summary="s" * 1_500, created_at=NOW + timedelta(minutes=index))
        for step in range(15 if index == 0 else 3):
            output = [{"summary": f"step {step}"}, {"output": {"text": "t" * 2_000}}, {"error": "boom"},
                      {"summary": "", "output": "fallback"}, {}][step % 5]
            db.add(m.RunStep, run_id=run.id, step_index=step, kind="action", status="completed",
                   input={}, output={**output, "data": {"big": "b" * 5_000}})
        action = db.add(m.Action, organization_id=ORG, agent_id=agent.id, run_id=run.id, status="pending_approval",
                        resource_id="r", scope="s", idempotency_key=f"k{index}", payload={})
        db.add(m.Approval, organization_id=ORG, action_id=action.id, status="pending",
               created_at=NOW + timedelta(minutes=index))
        result = db.add(m.Result, organization_id=ORG, worker_id=worker.id, job_id=job.id, latest_version=2,
                        status="completed", title=f"r{index}", created_at=NOW + timedelta(minutes=index))
        for version in (1, 2):
            db.add(m.ResultVersion, result_id=result.id, version=version,
                   body={"summary": f"v{version}" * 800, "blocks": ["x" * 5_000]} if index % 5 else {"blocks": []})
        policy = db.add(m.Policy, organization_id=ORG, name=f"p{index}", status="enabled", current_revision=2,
                        updated_at=NOW + timedelta(minutes=index))
        for revision in (1, 2):
            db.add(m.PolicyRevision, policy_id=policy.id, revision=revision, effect="allow", priority=index,
                   selectors={"scopes": [f"s{revision}"], "_meta": {"description": "d" * 900}})
        db.add(m.Memory, organization_id=ORG, owner_id=str(worker.id), scope="worker", title="m", content="c",
               status="active", created_at=NOW + timedelta(minutes=index))
        db.add(m.WorkerDirective, organization_id=ORG, worker_id=worker.id, text="t" * 1_500, status="active",
               created_at=NOW + timedelta(minutes=index))
        db.add(m.CapabilityProfile, organization_id=ORG, agent_id=agent.id, scope=f"scope.{index}", active=True)
        db.add(m.Integration, organization_id=ORG, provider="browser", display_name=f"b{index}", status="connected")
    artifact = db.add(m.Artifact, organization_id=ORG, storage_key="k", media_type="image/png",
                      metadata_json={"name": "a.png", "hidden": "no"})
    db.add(m.ArtifactAnalysis, organization_id=ORG, artifact_id=artifact.id, status="completed",
           findings={"summary": "cat"})
    for index in range(size):
        db.add(m.ConversationMessage, organization_id=ORG, thread_id=thread.id, role="human" if index % 2 else "worker",
               content="hello " * 500, artifact_references=[{"id": str(artifact.id)}] if index == 0 else [],
               created_at=NOW + timedelta(minutes=index))
    return {"thread": thread, "human": human}


async def _assemble(db: Database, assembler: Any, seeded: dict[str, Any]) -> tuple[dict[str, Any], int]:
    with _aware_sqlite_datetimes():
        session = BenchSession(Session(db.engine, expire_on_commit=False))
        thread = session.sync_session.get(m.ConversationThread, seeded["thread"].id)
        db.statements.clear()
        context = await assembler(cast(Any, session)).assemble(organization_id=ORG, thread=thread,
                                                               user_id=seeded["human"].id)
        return context, len(db.statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True], ids=["organisation", "worker"])
async def test_context_is_unchanged_and_reads_a_fixed_number_of_queries(scoped: bool) -> None:
    counts = []
    for size in (4, 12):
        db = Database(TABLES)
        seeded = seed(db, size, scoped=scoped)
        expected, legacy_queries = await _assemble(db, LegacyAssembler, seeded)
        actual, queries = await _assemble(db, ConversationContextAssembler, seeded)
        # pendingClarifications is new in phase 3 (questions awaiting an answer); the rest is unchanged.
        assert actual.pop("pendingClarifications") == []
        # "runs" is new (after the first live test): how each job actually starts, from the backend.
        for job in actual["activeJobs"]:
            assert job.pop("runs") in {"on its schedule", "only when someone asks; no schedule or event trigger is set up"}
        assert actual == expected
        assert expected["recentRuns"] and expected["activeSchedules"] and expected["pendingApprovals"]
        assert legacy_queries > queries
        counts.append(queries)
    assert counts[0] == counts[1]


@pytest.mark.asyncio
async def test_run_steps_and_revisions_are_read_by_json_path_not_whole() -> None:
    db = Database(TABLES)
    seeded = seed(db, 3, scoped=False)
    await _assemble(db, ConversationContextAssembler, seeded)
    reads = " ".join(statement for statement in db.statements if "run_steps" in statement or "job_revisions" in statement)
    # The columns appear only inside JSON paths, never read whole.
    assert re.search(r"(?<!JSON_EXTRACT\()run_steps\.output", reads) is None
    assert re.search(r"(?<!JSON_EXTRACT\()job_revisions\.definition", reads) is None


@pytest.mark.asyncio
async def test_questions_awaiting_an_answer_are_listed_with_their_request() -> None:
    db = Database(TABLES)
    seeded = seed(db, 2, scoped=False)
    thread, human = seeded["thread"], seeded["human"]
    now = datetime.now(UTC)  # only questions from the last seven days are listed
    asked = db.add(m.ConversationMessage, organization_id=ORG, thread_id=thread.id, role="human",
                   content="Create a worker that posts our news", created_at=now)
    pending = db.add(m.ConversationCommand, organization_id=ORG, thread_id=thread.id, source_message_id=asked.id,
                     family="worker.create", status="clarification_required", created_by=human.id,
                     payload={}, receipt={"message": "Which site should it post to?"}, created_at=now)
    for status, age in (("completed", 0), ("clarification_required", 8)):
        db.add(m.ConversationCommand, organization_id=ORG, thread_id=thread.id, source_message_id=asked.id,
               family="job.stop", status=status, created_by=human.id, payload={}, receipt={},
               created_at=now - timedelta(days=age))
    context, _ = await _assemble(db, ConversationContextAssembler, seeded)
    assert context["pendingClarifications"] == [{"id": str(pending.id), "family": "worker.create",
        "question": "Which site should it post to?", "request": "Create a worker that posts our news"}]
