import asyncio

from app.bootstrap.settings import settings
from app.infrastructure.database.outbox import TransactionalOutbox
from app.infrastructure.database.session import session_factory
from app.infrastructure.qstash.provider import UpstashQStashProvider
from app.infrastructure.queue.broker import ProviderQueueBroker


async def publish_outbox_once() -> int:
    if settings.qstash_url is None or settings.qstash_token is None:
        return 0

    broker = ProviderQueueBroker(UpstashQStashProvider.from_settings())
    async with session_factory() as session:
        outbox = TransactionalOutbox(session)
        events = await outbox.unpublished(limit=100)
        for event in events:
            await broker.publish(
                event.topic,
                event.payload,
                idempotency_key=f"outbox:{event.id}",
            )
            await outbox.mark_published(event)
        await session.commit()
        return len(events)


def main() -> None:
    count = asyncio.run(publish_outbox_once())
    print(f"Aduoryn outbox published {count} event(s).")


if __name__ == "__main__":
    main()
