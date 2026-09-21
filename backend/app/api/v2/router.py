from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter()


@router.get("/system/status", tags=["system"])
async def system_status() -> dict[str, object]:
    return {
        "service": "audoryn",
        "version": "v2",
        "controlPlane": {"state": "operational", "securityMode": "fail-closed"},
        "architecture": {"style": "modular-monolith"},
        "lifecycle": {"current": "v2", "supported": ["v1", "v2"]},
        "checkedAt": datetime.now(UTC).isoformat(),
    }
