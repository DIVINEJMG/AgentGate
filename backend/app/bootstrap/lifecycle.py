from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.execution.bootstrap import execution_provider_registry
from app.execution.providers.base import ManagedExecutionProvider


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        for provider in execution_provider_registry().providers():
            if isinstance(provider, ManagedExecutionProvider):
                await provider.shutdown()
