from fastapi import APIRouter, Response, status

from app.api.github_routes import callback_router as github_callback_router
from app.api.integration_foundation_routes import event_router as integration_event_router
from app.api.internal.ai import router as internal_ai_router
from app.api.internal.console_reads import router as internal_console_reads_router
from app.api.internal.integrations import router as internal_integrations_router
from app.api.internal.migration import router as internal_migration_router
from app.api.internal.outbox import router as internal_outbox_router
from app.api.internal.runtime import router as internal_runtime_router
from app.api.internal.web_research import router as internal_web_research_router
from app.api.realtime_routes import ws_router as realtime_ws_router
from app.api.v1.router import router as v1_router
from app.api.v2.router import router as v2_router
from app.application.services.readiness import check_readiness

api_router = APIRouter()
api_router.include_router(integration_event_router)
api_router.include_router(github_callback_router)


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
api_router.include_router(internal_ai_router)
api_router.include_router(internal_console_reads_router)
api_router.include_router(internal_integrations_router)
api_router.include_router(internal_migration_router)
api_router.include_router(internal_outbox_router)
api_router.include_router(internal_runtime_router)
api_router.include_router(internal_web_research_router)
api_router.include_router(v1_router, prefix="/api/v1")
api_router.include_router(v2_router, prefix="/api/v2")
