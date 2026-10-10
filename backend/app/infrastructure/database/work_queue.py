from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.jobs.queue import ClaimedWorkItem, WorkItemQueue
from app.infrastructure.database.models import WorkItem
from app.runtime.admission import admission_order, due_work_order


class SQLAlchemyWorkItemQueue(WorkItemQueue):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _active_by_organization(self) -> Counter[UUID]:
        active = await self._session.scalars(
            select(WorkItem.organization_id).where(
                WorkItem.status.in_(["admitted", "running"])
            )
        )
        return Counter(active.all())

    async def list_due_ids(self, *, limit: int = 100) -> list[UUID]:
        now = datetime.now(UTC)
        statement = (
            select(WorkItem)
            .where(
                WorkItem.status == "queued",
                WorkItem.scheduled_at <= now,
            )
            .order_by(*due_work_order(now))
            .limit(max(limit, 1000))
        )
        items = list((await self._session.scalars(statement)).all())
        ordered = admission_order(
            items, now=datetime.now(UTC),
            active_by_organization=await self._active_by_organization(),
        )
        return [item.id for item in ordered[:limit]]

    async def claim_next(self) -> ClaimedWorkItem | None:
        now = datetime.now(UTC)
        statement = (
            select(WorkItem)
            .where(
                WorkItem.status == "queued",
                WorkItem.scheduled_at <= now,
            )
            .order_by(*due_work_order(now))
            .with_for_update(skip_locked=True)
            .limit(1000)
        )
        items = list((await self._session.scalars(statement)).all())
        if not items:
            return None
        model = admission_order(
            items, now=datetime.now(UTC),
            active_by_organization=await self._active_by_organization(),
        )[0]
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
