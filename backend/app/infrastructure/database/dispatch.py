from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.jobs.dispatch import ScheduledDispatch
from app.infrastructure.database.models import WorkItem


class WorkItemDispatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, dispatch: ScheduledDispatch) -> WorkItem:
        item = WorkItem(
            organization_id=dispatch.organization_id,
            job_id=dispatch.job_id,
            job_revision_id=dispatch.job_revision_id,
            status="queued",
            priority="normal",
            correlation_id=dispatch.correlation_id,
            scheduled_at=dispatch.scheduled_at,
            payload=dispatch.payload,
        )
        self._session.add(item)
        await self._session.flush()
        return item
