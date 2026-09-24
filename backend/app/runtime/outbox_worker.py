import asyncio

from app.bootstrap.settings import settings
from app.runtime.outbox_publisher import drain_outbox_batch


async def run_outbox_publisher() -> None:
    """Local/manual fallback only.

    Production uses QStash-triggered /internal/v1/outbox/drain plus the QStash
    recovery schedule. This loop remains useful for local development or emergency
    manual draining without changing the publishing implementation.
    """

    while True:
        count = await drain_outbox_batch(limit=100)
        if count == 0:
            await asyncio.sleep(settings.outbox_poll_seconds)
        else:
            await asyncio.sleep(0)


def main() -> None:
    asyncio.run(run_outbox_publisher())


if __name__ == "__main__":
    main()
