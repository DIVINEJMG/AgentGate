from app.application.queries.system_status import SystemStatus


def serialize_v1(status: SystemStatus) -> dict[str, object]:
    return {
        "service": status.service,
        "apiVersion": "v1",
        "status": status.state,
        "securityMode": status.security_mode,
        "architecture": status.architecture_style,
        "currentVersion": status.current_version,
        "supportedVersions": list(status.supported_versions),
        "migration": {
            "cutoverStage": status.cutover_stage,
            "shadowMode": status.shadow_mode,
        },
        "checkedAt": status.checked_at,
    }


def serialize_v2(status: SystemStatus) -> dict[str, object]:
    return {
        "service": status.service,
        "version": "v2",
        "controlPlane": {
            "state": status.state,
            "securityMode": status.security_mode,
        },
        "architecture": {"style": status.architecture_style},
        "lifecycle": {
            "current": status.current_version,
            "supported": list(status.supported_versions),
        },
        "migration": {
            "cutoverStage": status.cutover_stage,
            "shadowMode": status.shadow_mode,
        },
        "checkedAt": status.checked_at,
    }
