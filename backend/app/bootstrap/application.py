from fastapi import FastAPI

from app.api.router import api_router
from app.bootstrap.lifecycle import lifespan
from app.bootstrap.settings import settings


def create_application() -> FastAPI:
    application = FastAPI(
        title="Aduoryn API",
        version="26.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    application.include_router(api_router)
    return application
