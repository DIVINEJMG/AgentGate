import asyncio

from audoryn_execution.infrastructure.database.work_queue import SQLAlchemyWorkItemQueue
from audoryn_shared.application.services.cutover import CutoverController
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.infrastructure.database.session import session_factory
from audoryn_shared.infrastructure.redis.coordination import RedisCoordinator


async def schedule_once() -> int:
    CutoverController(settings.cutover_stage).require_authoritative("scheduler")
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
