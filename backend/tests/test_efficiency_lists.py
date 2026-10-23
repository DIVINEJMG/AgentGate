"""Efficiency pass, fix 4 and 5: the policies, actions, incidents, memories and results lists load
in a fixed number of queries with the same records, and preparation recovery lets an idle
database sleep.

As in test_efficiency_queries.py, the previous per-row code is kept here as the reference.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api.runtime_result_routes import (
    _memories_public,
    _memory_record,
    _result_details,
    _result_record,
)
from audoryn_execution.runtime.preparation_worker import RECOVERY_BUSY_SECONDS, next_recovery_delay
from audoryn_shared.api.governance_routes import (
    _action_record,
    _actions_public,
    _incident_record,
    _incidents_public,
    _policies_public,
    _policy_record,
)
from audoryn_shared.infrastructure.database import models as m
from tests.test_efficiency_queries import NOW, ORG, Database, _all

TABLES = [m.AgentIdentity, m.Worker, m.Job, m.Run, m.Action, m.Approval, m.Policy, m.PolicyRevision,
          m.Incident, m.Memory, m.AuditEvent, m.Result, m.ResultVersion]
OTHER_ORG = uuid4()


def seed(db: Database, size: int) -> dict[str, list[Any]]:
    agent = db.add(m.AgentIdentity, organization_id=ORG, name="Agent")
    worker = db.add(m.Worker, organization_id=ORG, agent_identity_id=agent.id, name="Ada", department="Ops")
    job = db.add(m.Job, organization_id=ORG, worker_id=worker.id, name="Daily report")
    seeded: dict[str, list[Any]] = {"policies": [], "actions": [], "incidents": [], "memories": [], "results": []}
    for index in range(size):
        policy = db.add(m.Policy, organization_id=ORG, name=f"policy {index}", status="enabled", current_revision=2)
        for revision in (1, 2):
            db.add(m.PolicyRevision, policy_id=policy.id, revision=revision, effect="allow" if revision == 2 else "deny",
                   priority=index, created_at=NOW, selectors={"actions": [f"a{revision}"], "_meta": {"description": f"v{revision}", "createdBy": "me"}})
        seeded["policies"].append(policy)

        action = db.add(m.Action, organization_id=ORG, agent_id=agent.id, status="executed", resource_id="r", scope="s",
                        idempotency_key=f"k{index}", payload={"request": {"action": "merge"}, "result": {"summary": "ok"}})
        if index % 2:
            db.add(m.Approval, organization_id=ORG, action_id=action.id, status="approved")
        seeded["actions"].append(action)

        incident = db.add(m.Incident, organization_id=ORG, status="open", severity="high", summary=f"s{index}")
        for minute, (kind, meta) in enumerate([("incident.created", {"title": f"T{index}", "createdBy": "u"}),
                                               ("incident.status.changed", {"acknowledgedBy": "v", "title": f"T{index}b"})][: 1 + index % 2]):
            db.add(m.AuditEvent, organization_id=ORG, event_type=kind, category="c", severity="info", actor={},
                   resource={"id": str(incident.id)}, payload={"metadata": meta, "large": "x" * 3_000}, created_at=NOW + timedelta(minutes=minute))
        # Same-type events about other incidents, and in another organisation, must not leak in.
        db.add(m.AuditEvent, organization_id=OTHER_ORG, event_type="incident.created", category="c", severity="info", actor={},
               resource={"id": str(incident.id)}, payload={"metadata": {"title": "WRONG ORG"}}, created_at=NOW)
        seeded["incidents"].append(incident)

        memory = db.add(m.Memory, organization_id=ORG, scope="worker", owner_id=str(worker.id), title=f"m{index}", content="c")
        events = [("memory.created", {"tags": ["a"], "createdBy": "u"}), ("memory.promoted", {"sourceMemoryId": "x"}), ("memory.archived", None)]
        for minute, (kind, meta) in enumerate(events[: 1 + index % 3]):
            db.add(m.AuditEvent, organization_id=ORG, event_type=kind, category="c", severity="info", actor={},
                   resource={"id": str(memory.id)}, payload={"metadata": meta} if meta is not None else {}, created_at=NOW + timedelta(minutes=minute))
        seeded["memories"].append(memory)

        result = db.add(m.Result, organization_id=ORG, worker_id=worker.id, job_id=job.id, latest_version=2, status="completed", title=f"r{index}")
        for version in (1, 2):
            db.add(m.ResultVersion, result_id=result.id, version=version, created_at=NOW,
                   body={"summary": f"v{version}", "blocks": [{"type": "text"}] * version, "runId": "r"})
        seeded["results"].append(result)
    # An incident and a memory with no events, and a result whose worker and job are missing.
    seeded["incidents"].append(db.add(m.Incident, organization_id=ORG, status="open", severity="low", summary="quiet"))
    seeded["memories"].append(db.add(m.Memory, organization_id=ORG, scope="run", owner_id="run-1", title="quiet", content="c"))
    seeded["results"].append(db.add(m.Result, organization_id=ORG, worker_id=uuid4(), job_id=uuid4(), latest_version=1, status="draft", title="orphan"))
    return seeded


# ---------- the previous per-row code, kept as the reference ----------

async def legacy_policy(session: Any, policy: Any) -> dict[str, Any]:
    revision = await session.scalar(select(m.PolicyRevision).where(m.PolicyRevision.policy_id == policy.id, m.PolicyRevision.revision == policy.current_revision))
    return _policy_record(policy, revision)


async def legacy_action(session: Any, action: Any) -> dict[str, Any]:
    agent = await session.get(m.AgentIdentity, action.agent_id)
    approval = await session.scalar(select(m.Approval).where(m.Approval.action_id == action.id))
    return _action_record(action, agent.name if agent else "", str(approval.id) if approval else None)


async def _org_events(session: Any, organization_id: Any, kinds: list[str]) -> list[Any]:
    return list((await session.scalars(select(m.AuditEvent).where(m.AuditEvent.organization_id == organization_id, m.AuditEvent.event_type.in_(kinds)).order_by(m.AuditEvent.created_at))).all())


async def legacy_incident(session: Any, incident: Any) -> dict[str, Any]:
    relevant = [e for e in await _org_events(session, incident.organization_id, ["incident.created", "incident.status.changed"]) if str((e.resource or {}).get("id", "")) == str(incident.id)]
    created = next((e for e in relevant if e.event_type == "incident.created"), None)
    latest = relevant[-1] if relevant else created
    created_meta = (created.payload or {}).get("metadata", {}) if created is not None else {}
    latest_meta = (latest.payload or {}).get("metadata", {}) if latest is not None else {}
    return _incident_record(incident, {**(created_meta if isinstance(created_meta, dict) else {}), **(latest_meta if isinstance(latest_meta, dict) else {})})


async def legacy_memory(session: Any, memory: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for event in await _org_events(session, memory.organization_id, ["memory.created", "memory.archived", "memory.promoted"]):
        if str((event.resource or {}).get("id", "")) != str(memory.id):
            continue
        raw = (event.payload or {}).get("metadata", {})
        if isinstance(raw, dict):
            metadata.update(raw)
        if event.event_type == "memory.archived":
            metadata["status"] = "archived"
        if event.event_type == "memory.promoted":
            metadata["source"] = "promoted"
    return _memory_record(memory, metadata)


async def legacy_result(session: Any, result: Any) -> dict[str, Any]:
    worker = await session.get(m.Worker, result.worker_id)
    job = await session.get(m.Job, result.job_id)
    version = await session.scalar(select(m.ResultVersion).where(m.ResultVersion.result_id == result.id, m.ResultVersion.version == result.latest_version))
    return _result_record(result, worker, job.name if job else None, version.body if version else None)


CASES = [
    ("policies", m.Policy, legacy_policy, _policies_public, 1),
    ("actions", m.Action, legacy_action, _actions_public, 2),
    ("incidents", m.Incident, legacy_incident, _incidents_public, 1),
    ("memories", m.Memory, legacy_memory, _memories_public, 1),
    ("results", m.Result, legacy_result, _result_details, 3),
]


@pytest.mark.parametrize("size", [3, 9])
@pytest.mark.parametrize(("name", "model", "legacy", "batched", "queries"), CASES, ids=[case[0] for case in CASES])
def test_each_list_is_unchanged_and_takes_a_fixed_number_of_queries(name: str, model: Any, legacy: Any, batched: Any, queries: int, size: int) -> None:
    db = Database(TABLES)
    rows = seed(db, size)[name]
    expected, legacy_queries = db.count(model, rows, lambda session, fresh: _all(legacy, session, fresh))
    actual, count = db.count(model, rows, batched)
    assert actual == expected
    assert count == queries
    assert legacy_queries >= len(rows)


def test_incident_and_memory_events_are_read_only_for_the_listed_rows_and_never_whole() -> None:
    db = Database(TABLES)
    seeded = seed(db, 4)
    for name, model, batched in (("incidents", m.Incident, _incidents_public), ("memories", m.Memory, _memories_public)):
        db.count(model, seeded[name], batched)
        [statement] = [s for s in db.statements if "audit_events" in s]
        # The payload is only read through a JSON path (its "metadata" part), never whole...
        assert re.search(r"(?<!JSON_EXTRACT\()audit_events\.payload", statement) is None
        # ...and only the events about the listed rows are fetched.
        assert "audit_events.resource ->>" in statement


@pytest.mark.parametrize(("started", "active", "retry_in", "expected"), [
    (2, 0, None, RECOVERY_BUSY_SECONDS),      # work found: check again soon
    (0, 1, None, RECOVERY_BUSY_SECONDS),      # a preparation still running
    (0, 0, None, 600),                        # nothing pending: let the database sleep
    (0, 0, 120, 121),                         # a retry is due in two minutes
    (0, 0, 3, RECOVERY_BUSY_SECONDS),         # never faster than the busy pace
    (0, 0, 7_200, 600),                       # never longer than the idle pace
])
def test_recovery_waits_long_when_idle_and_wakes_for_scheduled_retries(started: int, active: int, retry_in: int | None, expected: float) -> None:
    now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    next_retry_at = now + timedelta(seconds=retry_in) if retry_in is not None else None
    assert next_recovery_delay(started=started, active=active, next_retry_at=next_retry_at, now=now, idle_seconds=600) == expected


def test_the_idle_pace_lets_neon_suspend_the_compute() -> None:
    from audoryn_shared.bootstrap.settings import Settings

    # Neon suspends a compute after five minutes without queries; the default must exceed that.
    assert Settings.model_fields["preparation_recovery_idle_seconds"].default > 300
