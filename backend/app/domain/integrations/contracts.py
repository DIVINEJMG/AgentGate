from dataclasses import dataclass
from typing import Literal, Protocol

RiskLevel = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True, slots=True)
class IntegrationManifest:
    provider: str
    resource: str
    capability: str
    action: str
    scope: str
    risk: RiskLevel
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    side_effect: bool
    approval_default: bool


@dataclass(frozen=True, slots=True)
class IntegrationExecutionResult:
    provider_operation: str
    summary: str
    data: object


class IntegrationAdapter(Protocol):
    provider: str
    manifests: tuple[IntegrationManifest, ...]

    async def execute(
        self,
        *,
        operation: str,
        configuration: dict[str, str],
        credential: str | None,
        payload: dict[str, object],
    ) -> IntegrationExecutionResult: ...
