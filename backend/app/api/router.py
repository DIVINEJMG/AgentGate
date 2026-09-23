from fastapi import APIRouter, Response, status

from app.api.internal.migration import router as internal_migration_router
from app.api.internal.runtime import router as internal_runtime_router
from app.api.realtime_routes import ws_router as realtime_ws_router
from app.api.v1.router import router as v1_router
from app.api.v2.router import router as v2_router
from app.application.services.readiness import check_readiness

api_router = APIRouter()


@api_router.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok", "service": "audoryn-api"}


@api_router.get("/health/ready", tags=["health"])
async def ready(response: Response) -> dict[str, object]:
    readiness = await check_readiness()
    if not readiness.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if readiness.ready else "not_ready",
        "service": "audoryn-api",
        "dependencies": readiness.as_dict(),
    }

api_router.include_router(realtime_ws_router)
api_router.include_router(internal_migration_router)
api_router.include_router(internal_runtime_router)
api_router.include_router(v1_router, prefix="/api/v1")
api_router.include_router(v2_router, prefix="/api/v2")
