from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.product_common import utcnow
from app.infrastructure.database.models import (
    ConversationMessage,
    ConversationThread,
    Job,
    JobRevision,
    Result,
    Run,
    Worker,
    WorkItem,
)
from app.infrastructure.database.outbox import TransactionalOutbox


def _uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def live_result_reference(
    *,
    result: Result,
    run: Run,
    item: WorkItem,
    job: Job,
    worker: Worker,
    summary: str,
) -> dict[str, Any]:
    return {
        "type": "result",
        "id": str(result.id),
        "name": result.title,
        "presentation": "live_result",
        "status": "completed",
        "summary": summary[:2000],
        "workerId": str(worker.id),
        "jobId": str(job.id),
        "workItemId": str(item.id),
        "runId": str(run.id),
    }


def _contains_live_result(
    message: ConversationMessage,
    result_id: UUID,
) -> bool:
    for raw in list(message.result_references or []):
        if not isinstance(raw, dict):
            continue
        if (
            str(raw.get("id") or "") == str(result_id)
            and raw.get("type") == "result"
            and raw.get("presentation") == "live_result"
        ):
            return True
    return False


async def _delivery_thread(
    session: AsyncSession,
    *,
    item: WorkItem,
    job: Job,
    worker: Worker,
) -> ConversationThread | None:
    revision = await session.scalar(
        select(JobRevision).where(
            JobRevision.job_id == job.id,
            JobRevision.revision == job.current_revision,
        )
    )
    if revision is None:
        return None

    definition = dict(revision.definition or {}) if isinstance(revision.definition, dict) else {}
    raw_autonomy = definition.get("autonomy")
    autonomy = dict(raw_autonomy) if isinstance(raw_autonomy, dict) else {}

    source_thread_id = _uuid(autonomy.get("sourceThreadId"))
    if source_thread_id is not None:
        source = await session.get(ConversationThread, source_thread_id)
        if (
            source is not None
            and source.organization_id == item.organization_id
            and source.worker_id == worker.id
            and source.status == "active"
        ):
            return source

    existing = await session.scalar(
        select(ConversationThread)
        .where(
            ConversationThread.organization_id == item.organization_id,
            ConversationThread.worker_id == worker.id,
            ConversationThread.created_by == revision.created_by,
            ConversationThread.status == "active",
        )
        .order_by(desc(ConversationThread.updated_at))
        .limit(1)
    )
    if existing is not None:
        return existing

    thread = ConversationThread(
        organization_id=item.organization_id,
        worker_id=worker.id,
        title=f"{worker.name} results"[:200],
        status="active",
        created_by=revision.created_by,
    )
    session.add(thread)
    await session.flush()
    await TransactionalOutbox(session).enqueue(
        topic="conversation.thread.created",
        aggregate_type="conversation_thread",
        aggregate_id=str(thread.id),
        payload={
            "organization_id": str(item.organization_id),
            "worker_id": str(worker.id),
            "resource_id": str(thread.id),
            "thread_id": str(thread.id),
        },
    )
    return thread


async def append_live_result_message(
    session: AsyncSession,
    *,
    item: WorkItem,
    run: Run,
    job: Job,
    worker: Worker,
    result: Result,
    summary: str,
) -> ConversationMessage | None:
    thread = await _delivery_thread(
        session,
        item=item,
        job=job,
        worker=worker,
    )
    if thread is None:
        return None

    recent = list(
        (
            await session.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.organization_id == item.organization_id,
                    ConversationMessage.thread_id == thread.id,
                )
                .order_by(desc(ConversationMessage.created_at))
                .limit(100)
            )
        ).all()
    )
    if any(_contains_live_result(message, result.id) for message in recent):
        return None

    reference = live_result_reference(
        result=result,
        run=run,
        item=item,
        job=job,
        worker=worker,
        summary=summary,
    )
    message = ConversationMessage(
        organization_id=item.organization_id,
        thread_id=thread.id,
        role="worker",
        content=summary[:12000] or f"{job.name} completed.",
        artifact_references=[],
        command_references=[],
        result_references=[reference],
    )
    session.add(message)
    thread.updated_at = utcnow()
    await session.flush()
    await TransactionalOutbox(session).enqueue(
        topic="conversation.response.created",
        aggregate_type="conversation_message",
        aggregate_id=str(message.id),
        payload={
            "organization_id": str(item.organization_id),
            "worker_id": str(worker.id),
            "resource_id": str(thread.id),
            "thread_id": str(thread.id),
            "message_id": str(message.id),
            "role": message.role,
            "presentation": "live_result",
            "result_id": str(result.id),
        },
    )
    return message
