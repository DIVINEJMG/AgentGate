from fastapi import APIRouter

from app.api.adapters.system_status import serialize_v1
from app.application.queries.system_status import get_system_status

router = APIRouter()


@router.get("/system/status", tags=["system"])
async def system_status() -> dict[str, object]:
    return serialize_v1(get_system_status())
