from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter()


@router.get("/system/status", tags=["system"])
async def system_status() -> dict[str, object]:
    return {
        "service": "audoryn",
        "apiVersion": "v1",
        "status": "operational",
        "securityMode": "fail-closed",
        "architecture": "modular-monolith",
        "currentVersion": "v2",
        "supportedVersions": ["v1", "v2"],
        "checkedAt": datetime.now(UTC).isoformat(),
    }
