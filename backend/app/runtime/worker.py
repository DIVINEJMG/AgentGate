import asyncio

from app.bootstrap.settings import settings
from app.infrastructure.database.session import session_factory
from app.infrastructure.database.work_queue import SQLAlchemyWorkItemQueue
from app.infrastructure.redis.coordination import RedisCoordinator
from app.runtime.executor import UnconfiguredRuntimeExecutor


async def work_once() -> bool:
    if not settings.runtime_execution_enabled:
        return False

    coordinator = RedisCoordinator.from_settings()
    try:
        async with session_factory() as session:
            queue = SQLAlchemyWorkItemQueue(session)
            work_item = await queue.claim_next()
            if work_item is None:
                return False

            await coordinator.heartbeat(
                f"work-item:{work_item.id}",
                "running",
                ttl_seconds=settings.worker_heartbeat_ttl_seconds,
            )

            executor = UnconfiguredRuntimeExecutor()
            try:
                await executor.execute(work_item)
            except RuntimeError as exc:
                await queue.checkpoint_failed(work_item.id, reason=str(exc))
                await session.commit()
                return True

            await queue.checkpoint_succeeded(work_item.id)
            await session.commit()
            return True
    finally:
        await coordinator.close()


async def run_worker() -> None:
    if not settings.runtime_execution_enabled:
        print("Aduoryn worker is disabled until runtime migration is enabled.")
        return

    while True:
        handled = await work_once()
        if not handled:
            await asyncio.sleep(settings.worker_poll_seconds)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
