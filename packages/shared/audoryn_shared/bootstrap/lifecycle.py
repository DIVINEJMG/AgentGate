import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.infrastructure.database.migration_runner import upgrade_database_schema


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Shared by every role. The execution service's part (audoryn_execution.service, when this
    process runs steps) starts after the migrations and stops after this process has stopped
    taking local steps and the watchdog, so a draining step never starts the next one here."""
    execution = getattr(application.state, "execution", None)
    if settings.database_migrate_on_startup:
        await upgrade_database_schema()
    if execution is not None:
        await execution.start()
    # Every role: it is armed by this process's own commits (../packages/shared/audoryn_shared/runtime/delivery_watchdog.py).
    watchdog = None
    if settings.delivery_watchdog_seconds:
        from audoryn_shared.runtime.delivery_watchdog import delivery_watchdog_loop
        watchdog = asyncio.create_task(delivery_watchdog_loop())
    from audoryn_shared.runtime import outbox_trigger
    outbox_trigger.serving = True
    try:
        yield
    finally:
        outbox_trigger.serving = False
        if watchdog:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
        if execution is not None:
            await execution.stop()
