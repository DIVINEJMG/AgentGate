from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bootstrap.settings import settings
from app.execution.bootstrap import execution_provider_registry
from app.execution.providers.base import ManagedExecutionProvider
from app.infrastructure.database.migration_runner import upgrade_database_schema


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.database_migrate_on_startup:
        await upgrade_database_schema()
    try:
        yield
    finally:
        for provider in execution_provider_registry().providers():
            if isinstance(provider, ManagedExecutionProvider):
                await provider.shutdown()
