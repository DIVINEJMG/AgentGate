from __future__ import annotations

from uuid import UUID

from app.infrastructure.database.outbox import TransactionalOutbox
from app.infrastructure.database.session import session_factory
from app.realtime.bus import RedisRealtimeBus
from app.realtime.contracts import REALTIME_EVENT_TYPES, RealtimeEvent


def _uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _publish_realtime(
    topic: str,
    payload: dict[str, object],
    *,
    event_id: str,
) -> None:
    if topic not in REALTIME_EVENT_TYPES:
        return
    organization_id = _uuid(payload.get("organization_id") or payload.get("organizationId"))
    if organization_id is None:
        return

    bus = RedisRealtimeBus.from_settings()
    try:
        await bus.emit(
            RealtimeEvent(
                event_type=topic,  # type: ignore[arg-type]
                event_id=event_id,
                organization_id=organization_id,
                worker_id=_uuid(payload.get("worker_id") or payload.get("workerId")),
                job_id=_uuid(payload.get("job_id") or payload.get("jobId")),
                run_id=_uuid(payload.get("run_id") or payload.get("runId")),
                resource_id=(
                    str(payload.get("resource_id") or payload.get("resourceId"))
                    if payload.get("resource_id") or payload.get("resourceId")
                    else None
                ),
                correlation_id=(
                    str(payload.get("correlation_id") or payload.get("correlationId"))
                    if payload.get("correlation_id") or payload.get("correlationId")
                    else None
                ),
                payload=payload,
            )
        )
    finally:
        await bus.close()


async def drain_outbox_batch(*, limit: int = 100) -> int:
    """Publish committed outbox rows to Redis and mark them published atomically.

    The caller must invoke this only after business transactions have committed.
    Duplicate delivery is safe because RedisRealtimeBus deduplicates on event_id.
    """

    async with session_factory() as session:
        outbox = TransactionalOutbox(session)
        events = await outbox.unpublished(limit=limit)
        for event in events:
            payload = dict(event.payload) if isinstance(event.payload, dict) else {}
            await _publish_realtime(
                event.topic,
                payload,
                event_id=str(event.id),
            )
            await outbox.mark_published(event)

        await session.commit()
        return len(events)
