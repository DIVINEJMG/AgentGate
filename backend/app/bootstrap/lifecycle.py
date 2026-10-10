import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bootstrap.settings import settings
from app.execution.bootstrap import execution_provider_registry
from app.execution.providers.base import ManagedExecutionProvider
from app.infrastructure.database.migration_runner import upgrade_database_schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.database_migrate_on_startup:
        await upgrade_database_schema()
    if settings.runtime_execution_enabled:
        logger.info(
            "Governed Chromium runtime is lazy; browser process will start on first browser action."
        )
    preparation_recovery = None
    if settings.smart_planner_enabled and settings.integration_foundation_enabled:
        from app.runtime.preparation_worker import preparation_recovery_loop
        preparation_recovery = asyncio.create_task(preparation_recovery_loop())
    try:
        yield
    finally:
        from app.api.internal.runtime import stop_runtime_tasks

        await stop_runtime_tasks()
        if preparation_recovery:
            preparation_recovery.cancel()
            await asyncio.gather(preparation_recovery, return_exceptions=True)
            from app.runtime.preparation_worker import stop_preparations
            await stop_preparations()
        for provider in execution_provider_registry().providers():
            if isinstance(provider, ManagedExecutionProvider):
                await provider.shutdown()
