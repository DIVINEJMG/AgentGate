"""Improvement plan, phase 4: approvals can be edited, batched, always allowed (narrowly), and do
not wait for ever. End-to-end runs are in the offline benchmark (approve-with-edit,
approval-expires, always-allow); these tests cover the rules around them."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from audoryn_execution.application.services import approval_expiry
from audoryn_shared.api import governance_routes
from audoryn_shared.domain.identity.principals import HumanPrincipal
from audoryn_shared.execution.providers.registry import ProviderRegistry
from audoryn_shared.infrastructure.database import models as m
from audoryn_shared.infrastructure.database.base import Base
from scripts.runtime_benchmark import BenchProvider, BenchSession, _aware_sqlite_datetimes
from tests.test_efficiency_queries import ORG, Database

TABLES = [mapper.class_ for mapper in Base.registry.mappers]


class Approvals:
    """Pending approvals for bench-store writes, in SQLite."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, policies: dict[str, int] | None = None) -> None:
        provider = BenchProvider()
        registry = ProviderRegistry((provider,))
        for module in ("audoryn_shared.execution.bootstrap", "audoryn_shared.api.integration_capability_routes", "audoryn_shared.api.governance_routes"):
            monkeypatch.setattr(f"{module}.execution_provider_registry", lambda: registry, raising=False)
        self.woken: list[Any] = []

        async def wake(**kwargs: Any) -> str:
            self.woken.append(kwargs)
            return "message"

        monkeypatch.setattr(governance_routes, "request_runtime_execution", wake)
        self.db = Database(TABLES)
        self.human = self.db.add(m.HumanIdentity, display_name="Owner")
        self.agent = self.db.add(m.AgentIdentity, organization_id=ORG, name="Ada", status="active", created_by=self.human.id)
        self.db.add(m.CapabilityProfile, organization_id=ORG, agent_id=self.agent.id, scope="bench.record.write", active=True)
        self.integration = self.db.add(m.Integration, organization_id=ORG, provider="bench", display_name="Bench store",
                                       status="connected", config={})
        for effect, priority in (policies or {"require_approval": 5}).items():
            policy = self.db.add(m.Policy, organization_id=ORG, name=effect, status="enabled", current_revision=1)
            self.db.add(m.PolicyRevision, policy_id=policy.id, revision=1, effect=effect, priority=priority,
                        selectors={"scopes": ["bench.record.write"]}, created_at=datetime.now(UTC))

    def pending(self, *, age_hours: float = 0, value: str = "draft") -> m.Approval:
        action = self.db.add(m.Action, organization_id=ORG, agent_id=self.agent.id, status="held",
                             resource_id=str(self.integration.id), scope="bench.record.write",
                             idempotency_key=f"k-{uuid4()}", payload={"request": {
                                 "provider": "bench", "operation": "bench.record.write", "scope": "bench.record.write",
                                 "resourceId": str(self.integration.id), "input": {"key": "b", "value": value}},
                                 "actionFingerprint": "f"})
        return self.db.add(m.Approval, organization_id=ORG, action_id=action.id, status="pending",
                           created_at=datetime.now(UTC) - timedelta(hours=age_hours))

    def principal(self, *permissions: str) -> HumanPrincipal:
        return HumanPrincipal(user_id=self.human.id, organization_id=ORG, membership_id=uuid4(), role="owner",
                              permissions=frozenset({"approvals.review", *permissions}))

    async def decide(self, approval: m.Approval, payload: dict[str, Any], *permissions: str) -> m.Approval:
        with _aware_sqlite_datetimes():
            session = BenchSession(Session(self.db.engine, expire_on_commit=False))
            return await governance_routes._decide_approval(cast(Any, session), ORG, self.principal(*permissions),
                                                            approval.id, payload)

    def action(self, approval: m.Approval) -> m.Action:
        with Session(self.db.engine) as session:
            action = session.get(m.Action, approval.action_id)
            assert action is not None
            return action


@pytest.mark.asyncio
async def test_an_edit_is_checked_saved_and_bound_to_its_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    approvals = Approvals(monkeypatch)
    approval = approvals.pending()
    decided = await approvals.decide(approval, {"decision": "approve", "input": {"key": "b", "value": "corrected"}})
    assert decided.status == "approved"
    data = approvals.action(approval).payload
    assert data["request"]["input"] == {"key": "b", "value": "corrected"}
    assert data["approvedEdit"]["previousInput"] == {"key": "b", "value": "draft"}
    assert data["approvedEdit"]["inputHash"] == governance_routes.input_hash({"key": "b", "value": "corrected"})
    with Session(approvals.db.engine) as session:
        assert session.scalar(select(m.AuditEvent.event_type).where(m.AuditEvent.event_type == "approval.action_edited"))


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "message"), [
    ({"decision": "approve", "input": {"key": "b", "colour": "red"}}, "not valid for this action"),
    ({"decision": "approve", "input": "free text"}, "must be an object"),
    ({"decision": "reject", "input": {"key": "b", "value": "x"}}, "Only an approval"),
])
async def test_an_invalid_edit_is_refused_and_nothing_changes(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], message: str) -> None:
    approvals = Approvals(monkeypatch)
    approval = approvals.pending()
    with pytest.raises(HTTPException, match=message):
        await approvals.decide(approval, payload)
    with Session(approvals.db.engine) as session:
        assert session.scalar(select(m.Approval.status).where(m.Approval.id == approval.id)) == "pending"


@pytest.mark.asyncio
async def test_an_unchanged_edit_is_an_ordinary_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    approvals = Approvals(monkeypatch)
    approval = approvals.pending()
    await approvals.decide(approval, {"decision": "approve", "input": {"key": "b", "value": "draft"}})
    assert "approvedEdit" not in approvals.action(approval).payload


@pytest.mark.asyncio
async def test_always_allow_creates_one_narrow_policy_above_the_approval_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    approvals = Approvals(monkeypatch)
    approval = approvals.pending()
    with pytest.raises(HTTPException) as refused:  # needs the right to manage policies
        await approvals.decide(approval, {"decision": "approve", "alwaysAllow": True})
    assert refused.value.status_code == 403
    await approvals.decide(approvals.pending(), {"decision": "approve", "alwaysAllow": True}, "policies.manage")
    with Session(approvals.db.engine) as session:
        [policy] = session.scalars(select(m.Policy).where(m.Policy.name.startswith("Always allow"))).all()
        revision = session.scalar(select(m.PolicyRevision).where(m.PolicyRevision.policy_id == policy.id))
        assert revision is not None and revision.effect == "allow" and revision.priority == 6
        assert {key: revision.selectors[key] for key in ("agentIds", "resourceIds", "scopes")} == {
            "agentIds": [str(approvals.agent.id)], "resourceIds": [str(approvals.integration.id)],
            "scopes": ["bench.record.write"]}


@pytest.mark.asyncio
async def test_always_allow_is_refused_when_policy_now_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    approvals = Approvals(monkeypatch, policies={"require_approval": 5, "deny": 9})
    with pytest.raises(HTTPException, match="now denies"):
        await approvals.decide(approvals.pending(), {"decision": "approve", "alwaysAllow": True}, "policies.manage")


@pytest.mark.asyncio
async def test_always_allow_is_refused_for_actions_that_always_need_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    approvals = Approvals(monkeypatch)
    provider = BenchProvider()
    read, write = provider.manifest.capabilities
    provider.manifest = replace(provider.manifest, capabilities=(read, replace(write, approval_recommendation="required")))
    monkeypatch.setattr("audoryn_shared.execution.bootstrap.execution_provider_registry", lambda: ProviderRegistry((provider,)))
    with pytest.raises(HTTPException, match="always needs approval"):
        await approvals.decide(approvals.pending(), {"decision": "approve", "alwaysAllow": True}, "policies.manage")


@pytest.mark.asyncio
async def test_several_approvals_are_decided_at_once_and_failures_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    approvals = Approvals(monkeypatch)
    first, second = approvals.pending(), approvals.pending()
    done = approvals.pending()
    await approvals.decide(done, {"decision": "reject"})
    with _aware_sqlite_datetimes():
        session = BenchSession(Session(approvals.db.engine, expire_on_commit=False))
        result = await governance_routes.approval_decisions_v1(
            ORG, {"approvalIds": [str(first.id), str(second.id), str(done.id), "not-an-id"], "decision": "approve"},
            approvals.principal(), cast(Any, session))
    assert sorted(item["id"] for item in result["approvals"]) == sorted([str(first.id), str(second.id)])
    assert [item["approvalId"] for item in result["failed"]] == [str(done.id), "not-an-id"]
    with pytest.raises(HTTPException):
        await governance_routes.approval_decisions_v1(ORG, {"approvalIds": [], "decision": "approve"},
                                                      approvals.principal(), cast(Any, session))


@pytest.mark.asyncio
async def test_a_waiting_approval_is_reminded_once_then_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    approvals = Approvals(monkeypatch)
    thread_id = uuid4()

    async def threads(session: Any, actions: list[Any]) -> dict[Any, Any]:
        return {action.id: str(thread_id) for action in actions}

    monkeypatch.setattr(approval_expiry, "approval_conversation_ids", threads)
    fresh, waiting, overdue = approvals.pending(age_hours=1), approvals.pending(age_hours=13), approvals.pending(age_hours=25)
    now = datetime.now(UTC)

    async def sweep() -> list[Any]:
        with _aware_sqlite_datetimes():
            session = BenchSession(Session(approvals.db.engine, expire_on_commit=False))
            return await approval_expiry.expire_and_remind(cast(Any, session), now=now)

    item = _waiting_task(approvals, overdue)
    assert await sweep() == [(ORG, item.id)]  # the expired approval's task is woken to finish
    assert await sweep() == []  # a second pass does not remind or expire again
    with Session(approvals.db.engine) as session:
        statuses = {row.id: row.status for row in session.scalars(select(m.Approval))}
        messages = session.scalars(select(m.ConversationMessage.content).where(m.ConversationMessage.thread_id == thread_id)).all()
    assert (statuses[fresh.id], statuses[waiting.id], statuses[overdue.id]) == ("pending", "pending", "expired")
    assert len([text for text in messages if text.startswith("Reminder:")]) == 1
    assert len([text for text in messages if "cancelled" in text and "Nothing was performed" in text]) == 1
    assert approvals.action(overdue).status == "blocked"
    record = governance_routes._approval_notification(approvals.action(waiting))
    assert record["state"] == "sent" and record["sent"] == 1
    with pytest.raises(HTTPException, match="expired"):
        await approvals.decide(overdue, {"decision": "approve"})


def test_the_approval_record_says_when_it_expires_and_what_can_be_edited(monkeypatch: pytest.MonkeyPatch) -> None:
    approvals = Approvals(monkeypatch)
    approval = approvals.pending()
    with Session(approvals.db.engine) as session:
        row = session.get(m.Approval, approval.id)
        assert row is not None
        lifecycle = governance_routes._approval_lifecycle(row, approvals.action(approval))
    assert lifecycle["edited"] is False and lifecycle["inputSchema"]["properties"]["value"] == {"type": "string"}
    assert datetime.fromisoformat(lifecycle["expiresAt"]) - row.created_at == timedelta(hours=24)


@pytest.mark.asyncio
@pytest.mark.parametrize("tampered", [False, True])
async def test_the_runtime_runs_an_edit_only_while_it_matches_what_was_approved(tampered: bool) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from audoryn_execution.runtime.managed import ManagedRuntimeExecutor

    approved = {"key": "b", "value": "corrected"}
    action = SimpleNamespace(id=uuid4(), scope="bench.record.write", resource_id="r", idempotency_key="k", payload={
        "request": {"provider": "bench", "operation": "bench.record.write", "input": {**approved, "value": "changed later"} if tampered else approved},
        "approvedEdit": {"inputHash": governance_routes.input_hash(approved)}, "actionFingerprint": "old"})
    approval = SimpleNamespace(id=uuid4(), status="approved")
    executor = cast(Any, object.__new__(ManagedRuntimeExecutor))
    executor._session = SimpleNamespace(get=AsyncMock(return_value=action), scalar=AsyncMock(return_value=approval))
    executor._fail = AsyncMock()
    resume = cast(Any, ManagedRuntimeExecutor._resume_approved_action)
    executor._universal_request = AsyncMock(side_effect=AssertionError("prepared") if tampered else None)
    item = SimpleNamespace(id=uuid4(), organization_id=ORG, correlation_id="c")
    step = SimpleNamespace(output={"actionId": str(action.id)})
    if tampered:
        outcome = await resume(executor, item=item, run=SimpleNamespace(id=uuid4()),
            step=step, job=None, worker=None, agent=SimpleNamespace(id=uuid4()), current_step=0)
        assert outcome.state == "failed" and "no longer matches" in cast(Any, executor._fail.await_args).kwargs["message"]
    else:
        with pytest.raises(TypeError):  # reaches preparation with the approved input (the stub returns None)
            await resume(executor, item=item, run=SimpleNamespace(id=uuid4()),
                step=step, job=None, worker=None, agent=SimpleNamespace(id=uuid4()), current_step=0)
        assert cast(Any, executor._universal_request.await_args).kwargs["proposal"].payload == approved


def _waiting_task(approvals: Approvals, approval: m.Approval) -> m.WorkItem:
    """A task paused at step 0 on this approval (its run and step point at the approval)."""
    item = approvals.db.add(m.WorkItem, organization_id=ORG, job_id=uuid4(), job_revision_id=uuid4(), status="waiting_approval",
                            priority=0, correlation_id=f"c-{uuid4()}", idempotency_key=f"i-{uuid4()}",
                            scheduled_at=datetime.now(UTC) - timedelta(minutes=1), payload={"runtime": {"currentStep": 0}})
    run = approvals.db.add(m.Run, organization_id=ORG, work_item_id=item.id, status="waiting_approval", correlation_id="c")
    approvals.db.add(m.RunStep, run_id=run.id, step_index=0, kind="action", status="waiting_approval",
                     input={}, output={"approvalId": str(approval.id)})
    with Session(approvals.db.engine) as session:
        action = session.get(m.Action, approval.action_id)
        assert action is not None
        action.run_id = run.id
        session.commit()
    return item


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_either_decision_wakes_the_waiting_task(monkeypatch: pytest.MonkeyPatch, decision: str) -> None:
    """F28: a rejection must wake the task too, so the runtime closes the step instead of the task
    waiting for good."""
    approvals = Approvals(monkeypatch)
    approval = approvals.pending()
    item = _waiting_task(approvals, approval)
    await approvals.decide(approval, {"decision": decision})
    assert [wake["work_item_id"] for wake in approvals.woken] == [item.id]


@pytest.mark.asyncio
async def test_the_sweep_resumes_tasks_whose_approval_was_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    """F28: the sweep resumes an approved or a rejected task (in three queries for all of them),
    and leaves one still waiting for a decision."""
    from audoryn_execution.api.internal.runtime import _decided_approval_items

    approvals = Approvals(monkeypatch)
    tasks = {}
    for status in ("approved", "rejected", "pending"):
        approval = approvals.pending()
        tasks[status] = _waiting_task(approvals, approval)
        with Session(approvals.db.engine) as session:
            row = session.get(m.Approval, approval.id)
            assert row is not None
            row.status = status
            session.commit()
    with _aware_sqlite_datetimes():
        session = BenchSession(Session(approvals.db.engine, expire_on_commit=False))
        waiting = list(await session.scalars(select(m.WorkItem)))
        assert await _decided_approval_items(session, waiting) == {tasks["approved"].id, tasks["rejected"].id}
        assert await _decided_approval_items(session, []) == set()
