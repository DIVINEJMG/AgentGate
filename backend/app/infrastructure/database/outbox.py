from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import OutboxEvent


class TransactionalOutbox:
    """Writes events in the caller's existing PostgreSQL transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        *,
        topic: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
    ) -> OutboxEvent:
        event = OutboxEvent(
            topic=topic,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=dict(payload),
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def unpublished(self, *, limit: int = 100) -> list[OutboxEvent]:
        rows = await self._session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows)

    async def mark_published(self, event: OutboxEvent) -> None:
        event.published_at = datetime.now(UTC)
        await self._session.flush()
