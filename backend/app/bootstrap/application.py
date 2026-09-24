from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap.lifecycle import lifespan
from app.bootstrap.settings import settings
from app.observability.middleware import CorrelationContextMiddleware


def create_application() -> FastAPI:
    # Resolve the root router lazily so application construction never captures
    # a partially initialized or stale router during import cycles/test reloads.
    from app.api.router import api_router

    application = FastAPI(
        title="Aduoryn API",
        version="26.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    application.add_middleware(CorrelationContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-ID"],
    )
    application.include_router(api_router)
    return application
