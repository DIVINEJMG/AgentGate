"""Builds an AgentGate process from its routes (shared by the core API and the execution service)."""
from collections.abc import Iterable
from types import ModuleType
from typing import Any

from fastapi import APIRouter, FastAPI

from audoryn_shared.bootstrap.health import health_router
from audoryn_shared.bootstrap.lifecycle import lifespan
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.observability.middleware import CorrelationContextMiddleware

Routers = Iterable[APIRouter | tuple[APIRouter, str]]


def build_application(routers: Routers, *, middleware: Iterable[tuple[type, dict[str, Any]]] = (),
                      execution: ModuleType | None = None) -> FastAPI:
    """`execution` is audoryn_execution.service when this process runs steps: its start and stop join
    the lifespan (audoryn_shared.bootstrap.lifecycle)."""
    application = FastAPI(
        title="Aduoryn API",
        version="26.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    application.state.execution = execution
    application.add_middleware(CorrelationContextMiddleware)
    for middleware_class, options in middleware:
        application.add_middleware(middleware_class, **options)
    application.include_router(health_router)
    for item in routers:
        router, prefix = item if isinstance(item, tuple) else (item, "")
        application.include_router(router, prefix=prefix)
    return application
