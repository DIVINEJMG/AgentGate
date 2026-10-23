"""Health checks, served by every AgentGate process whatever its role."""
from fastapi import APIRouter, Response, status

from audoryn_shared.application.services.readiness import check_readiness

health_router = APIRouter()


@health_router.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok", "service": "audoryn-api"}


@health_router.get("/health/ready", tags=["health"])
async def ready(response: Response) -> dict[str, object]:
    readiness = await check_readiness()
    if not readiness.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if readiness.ready else "not_ready",
        "service": "audoryn-api",
        "dependencies": readiness.as_dict(),
    }
