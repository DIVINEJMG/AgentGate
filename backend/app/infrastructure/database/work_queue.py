from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.jobs.queue import ClaimedWorkItem, WorkItemQueue
from app.infrastructure.database.models import WorkItem


class SQLAlchemyWorkItemQueue(WorkItemQueue):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_due_ids(self, *, limit: int = 100) -> list[UUID]:
        statement = (
            select(WorkItem.id)
            .where(
                WorkItem.status == "queued",
                WorkItem.scheduled_at <= datetime.now(UTC),
            )
            .order_by(WorkItem.scheduled_at, WorkItem.id)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def claim_next(self) -> ClaimedWorkItem | None:
        statement = (
            select(WorkItem)
            .where(
                WorkItem.status == "queued",
                WorkItem.scheduled_at <= datetime.now(UTC),
            )
            .order_by(WorkItem.scheduled_at, WorkItem.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        model = (await self._session.scalars(statement)).first()
        if model is None:
            return None
        model.status = "running"
        await self._session.flush()
        return ClaimedWorkItem(
            id=model.id,
            organization_id=model.organization_id,
            job_id=model.job_id,
            job_revision_id=model.job_revision_id,
            correlation_id=model.correlation_id,
            payload=dict(model.payload),
            scheduled_at=model.scheduled_at,
        )

    async def checkpoint_succeeded(self, work_item_id: UUID) -> None:
        model = await self._session.get(WorkItem, work_item_id, with_for_update=True)
        if model is None:
            raise LookupError("Work item not found.")
        if model.status != "running":
            raise RuntimeError("Only a running work item can succeed.")
        model.status = "succeeded"
        await self._session.flush()

    async def checkpoint_failed(self, work_item_id: UUID, *, reason: str) -> None:
        model = await self._session.get(WorkItem, work_item_id, with_for_update=True)
        if model is None:
            raise LookupError("Work item not found.")
        if model.status != "running":
            raise RuntimeError("Only a running work item can fail.")
        model.status = "failed"
        payload = dict(model.payload)
        payload["failure_reason"] = reason[:1000]
        model.payload = payload
        await self._session.flush()
