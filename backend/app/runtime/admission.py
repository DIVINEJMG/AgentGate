"""PostgreSQL-backed admission ordering for due Work Items."""

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import WorkItem

_PRIORITY = {"low": 0, "normal": 2, "high": 4, "urgent": 6}
_ADMISSION_LOCK = 918_273_645


def due_work_order(now: datetime):
    priority = case(
        (WorkItem.priority == "urgent", 6),
        (WorkItem.priority == "high", 4),
        (WorkItem.priority == "low", 0),
        else_=2,
    )
    age = func.least(
        12,
        func.greatest(
            0, func.floor(func.extract("epoch", now - WorkItem.created_at) / 300)
        ),
    )
    return ((priority + age).desc(), WorkItem.created_at, WorkItem.id)


def admission_order(
    items: list[WorkItem], *, now: datetime, active_by_organization: Counter[UUID]
) -> list[WorkItem]:
    """Age queued work and rotate organizations with active work behind idle ones."""

    def score(item: WorkItem) -> tuple[int, float, str]:
        created = item.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age_points = min(12, max(0, int((now - created).total_seconds() // 300)))
        # Active organizations yield near-equivalent work, while age still wins.
        return (
            _PRIORITY.get(item.priority, 2) + age_points
            - min(2, active_by_organization[item.organization_id]),
            -created.timestamp(),
            str(item.id),
        )

    return sorted(items, key=score, reverse=True)


async def admit_due_item(session: AsyncSession, item: WorkItem, *, now: datetime) -> bool:
    """Serialize admission across API processes; WorkItem is the durable authority."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMISSION_LOCK})
    queued = list((await session.scalars(
        select(WorkItem).where(
            WorkItem.status == "queued", WorkItem.scheduled_at <= now
        ).order_by(*due_work_order(now)).limit(1000).with_for_update()
    )).all())
    active = list((await session.scalars(
        select(WorkItem.organization_id).where(
            WorkItem.status.in_(["admitted", "running"])
        )
    )).all())
    order = admission_order(queued, now=now, active_by_organization=Counter(active))
    if not order or order[0].id != item.id:
        return False
    item.status = "admitted"
    await session.flush()
    return True
