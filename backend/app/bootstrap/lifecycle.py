import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bootstrap.settings import settings
from app.execution.bootstrap import execution_provider_registry
from app.execution.providers.base import ManagedExecutionProvider
from app.execution.providers.browser import browser_provider
from app.infrastructure.database.migration_runner import upgrade_database_schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.database_migrate_on_startup:
        await upgrade_database_schema()
    if settings.runtime_execution_enabled:
        browser_ready = await browser_provider.warmup()
        if browser_ready:
            logger.info("Governed Chromium runtime is ready before runtime traffic.")
        else:
            logger.error(
                "Governed Chromium runtime warmup failed; browser actions will fail closed."
            )
    try:
        yield
    finally:
        for provider in execution_provider_registry().providers():
            if isinstance(provider, ManagedExecutionProvider):
                await provider.shutdown()
