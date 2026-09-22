import asyncio

from app.infrastructure.database.session import session_factory
from app.infrastructure.database.work_queue import SQLAlchemyWorkItemQueue
from app.infrastructure.redis.coordination import RedisCoordinator


async def schedule_once() -> int:
    coordinator = RedisCoordinator.from_settings()
    try:
        async with session_factory() as session:
            queue = SQLAlchemyWorkItemQueue(session)
            due_ids = await queue.list_due_ids(limit=100)
        for work_item_id in due_ids:
            await coordinator.publish("work.available", str(work_item_id))
        return len(due_ids)
    finally:
        await coordinator.close()


def main() -> None:
    count = asyncio.run(schedule_once())
    print(f"Aduoryn scheduler announced {count} due work item(s).")


if __name__ == "__main__":
    main()
