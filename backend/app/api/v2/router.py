from fastapi import APIRouter

from app.api.adapters.system_status import serialize_v2
from app.api.auth_routes import router as auth_router
from app.api.organization_routes import v2_router as organization_router
from app.application.queries.system_status import get_system_status

router = APIRouter()
router.include_router(auth_router)
router.include_router(organization_router)


@router.get("/system/status", tags=["system"])
async def system_status() -> dict[str, object]:
    return serialize_v2(get_system_status())
