from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class SystemStatus:
    service: str
    state: str
    security_mode: str
    architecture_style: str
    current_version: str
    supported_versions: tuple[str, ...]
    checked_at: str


def get_system_status() -> SystemStatus:
    return SystemStatus(
        service="audoryn",
        state="operational",
        security_mode="fail-closed",
        architecture_style="modular-monolith",
        current_version="v2",
        supported_versions=("v1", "v2"),
        checked_at=datetime.now(UTC).isoformat(),
    )
