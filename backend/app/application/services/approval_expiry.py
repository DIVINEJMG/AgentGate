"""Approvals do not wait for ever (improvement plan, phase 4).

A pending approval gets a reminder in its conversation after `APPROVAL_REMINDER_HOURS` (default 12)
and expires after `APPROVAL_EXPIRY_HOURS` (default 24). Expiry cancels the action: nothing is
performed, and the task finishes as "needs attention" with what it already did. The runtime sweep
runs this every 10 minutes.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.product_common import append_audit
from app.application.services.approval_conversation import approval_conversation_ids
from app.bootstrap.settings import settings
from app.infrastructure.database.models import (
    Action,
    AgentIdentity,
    Approval,
    ConversationMessage,
    Run,
    WorkItem,
)
from app.infrastructure.database.outbox import TransactionalOutbox

BATCH = 50
SYSTEM = {"type": "system", "id": "approval-expiry"}


def expires_at(approval: Approval) -> datetime:
    created = approval.created_at if approval.created_at.tzinfo else approval.created_at.replace(tzinfo=UTC)
    return created + timedelta(hours=settings.approval_expiry_hours)


def _describe(action: Action, agent: AgentIdentity | None) -> str:
    request = dict((action.payload or {}).get("request") or {})
    what = str(request.get("operation") or action.scope).rsplit(".", 1)[-1].replace("_", " ")
    return f"{agent.name if agent else 'A worker'} is waiting for approval to {what} ({action.scope})"


async def expire_and_remind(session: AsyncSession, *, now: datetime) -> list[tuple[UUID, UUID]]:
    """Remind about, or expire, approvals that have waited too long. Returns (organization, work
    item) pairs whose runs must be woken to finish after an expiry."""
    reminder_age = timedelta(hours=settings.approval_reminder_hours)
    rows = (await session.execute(
        select(Approval, Action)
        .join(Action, Approval.action_id == Action.id)
        .where(Approval.status == "pending", Approval.created_at <= now - reminder_age)
        .order_by(Approval.created_at)
        .limit(BATCH)
    )).all()
    if not rows:
        return []
    threads = await approval_conversation_ids(session, [action for _, action in rows])
    agents = {agent.id: agent for agent in (await session.scalars(
        select(AgentIdentity).where(AgentIdentity.id.in_({action.agent_id for _, action in rows})))).all()}
    wake: list[tuple[UUID, UUID]] = []
    outbox = TransactionalOutbox(session)
    for approval, action in rows:
        data = dict(action.payload or {})
        described = _describe(action, agents.get(action.agent_id))
        if now >= expires_at(approval):
            approval.status = "expired"
            approval.decision_reason = f"Expired after {settings.approval_expiry_hours} hours without a decision."
            action.status = "blocked"
            action.payload = {**data, "error": "The approval expired; the action was not performed.",
                              "completedAt": now.isoformat()}
            await append_audit(session, organization_id=approval.organization_id, principal=None, actor=SYSTEM,
                event_type="approval.expired", category="approval", severity="warning", resource_type="approval",
                resource_id=str(approval.id), outcome="expired", summary=f"Approval expired: {described}.",
                metadata={"actionId": str(action.id), "hours": settings.approval_expiry_hours})
            await outbox.enqueue(topic="approval.decided", aggregate_type="approval", aggregate_id=str(approval.id),
                payload={"organization_id": str(approval.organization_id), "approval_id": str(approval.id),
                         "action_id": str(action.id), "decision": "expired"})
            await _post(session, outbox, approval, threads.get(action.id),
                f"{described}, but nobody decided within {settings.approval_expiry_hours} hours, so it was "
                "cancelled. Nothing was performed; ask again if it is still needed.")
            if action.run_id is not None:
                run = await session.get(Run, action.run_id)
                if run is not None:
                    wake.append((approval.organization_id, run.work_item_id))
        elif not data.get("approvalRemindedAt"):
            posted = await _post(session, outbox, approval, threads.get(action.id),
                f"Reminder: {described}. If nobody decides, it will be cancelled at "
                f"{expires_at(approval):%Y-%m-%d %H:%M} UTC.")
            action.payload = {**data, "approvalRemindedAt": now.isoformat(),
                              "approvalReminder": "posted" if posted else "no_conversation"}
    await session.commit()
    return wake


async def _post(session: AsyncSession, outbox: TransactionalOutbox, approval: Approval,
                thread_id: Any, content: str) -> bool:
    """A message in the conversation the approval belongs to. Returns whether there was one."""
    if not thread_id:
        return False
    message_id = uuid4()
    session.add(ConversationMessage(id=message_id, organization_id=approval.organization_id, thread_id=UUID(str(thread_id)),
        role="system", content=content, artifact_references=[], command_references=[], result_references=[]))
    await session.flush()
    await outbox.enqueue(topic="conversation.response.created", aggregate_type="conversation_message",
        aggregate_id=str(message_id), payload={"organization_id": str(approval.organization_id),
            "thread_id": str(thread_id), "message_id": str(message_id), "approval_id": str(approval.id)})
    return True


async def wake_expired(wake: list[tuple[UUID, UUID]]) -> None:
    """Let each run whose approval expired finish, from its saved step."""
    from app.infrastructure.database.session import session_factory
    from app.runtime.qstash_trigger import request_runtime_execution

    if not wake:
        return
    async with session_factory() as session:
        items = {item.id: item for item in (await session.scalars(
            select(WorkItem).where(WorkItem.id.in_([item_id for _, item_id in wake])))).all()}
    for organization_id, item_id in wake:
        item = items.get(item_id)
        runtime = dict((item.payload or {}).get("runtime") or {}) if item else {}
        await request_runtime_execution(organization_id=organization_id, work_item_id=item_id,
            expected_step=max(0, int(runtime.get("currentStep", 0))), reason="approval-expired")
