from fastapi import APIRouter

from app.api.internal.runtime import router as internal_runtime_router
from app.api.v1.router import router as v1_router
from app.api.v2.router import router as v2_router

api_router = APIRouter()


@api_router.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok", "service": "audoryn-api"}


@api_router.get("/health/ready", tags=["health"])
async def ready() -> dict[str, str]:
    return {"status": "ok", "service": "audoryn-api"}

api_router.include_router(internal_runtime_router)
api_router.include_router(v1_router, prefix="/api/v1")
api_router.include_router(v2_router, prefix="/api/v2")
