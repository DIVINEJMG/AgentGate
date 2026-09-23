import asyncio
from uuid import UUID

from app.bootstrap.settings import settings
from app.infrastructure.database.outbox import TransactionalOutbox
from app.infrastructure.database.session import session_factory
from app.infrastructure.qstash.provider import UpstashQStashProvider
from app.infrastructure.queue.broker import ProviderQueueBroker
from app.realtime.bus import RedisRealtimeBus
from app.realtime.contracts import REALTIME_EVENT_TYPES, RealtimeEvent


def _uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _publish_realtime(topic: str, payload: dict[str, object]) -> None:
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


async def publish_outbox_once() -> int:
    broker = (
        ProviderQueueBroker(UpstashQStashProvider.from_settings())
        if settings.qstash_url is not None and settings.qstash_token is not None
        else None
    )

    async with session_factory() as session:
        outbox = TransactionalOutbox(session)
        events = await outbox.unpublished(limit=100)
        for event in events:
            payload = dict(event.payload) if isinstance(event.payload, dict) else {}
            if broker is not None:
                await broker.publish(
                    event.topic,
                    payload,
                    idempotency_key=f"outbox:{event.id}",
                )

            # Outbox rows are created by earlier committed business transactions.
            # F29.18 mirrors normalized events into durable Streams + Pub/Sub only
            # from this publisher path, never from inside the business transaction.
            await _publish_realtime(event.topic, payload)
            await outbox.mark_published(event)

        await session.commit()
        return len(events)


def main() -> None:
    count = asyncio.run(publish_outbox_once())
    print(f"Aduoryn outbox published {count} event(s).")


if __name__ == "__main__":
    main()
