from fastapi import APIRouter

from app.api.adapters.system_status import serialize_v1
from app.api.agent_routes import v1_router as agent_router
from app.api.organization_routes import v1_router as organization_router
from app.application.queries.system_status import get_system_status

router = APIRouter()
router.include_router(agent_router)
router.include_router(organization_router)


@router.get("/system/status", tags=["system"])
async def system_status() -> dict[str, object]:
    return serialize_v1(get_system_status())
