from dataclasses import dataclass
from typing import Literal, Protocol

RiskLevel = Literal["low", "medium", "high", "critical"]
CredentialStrategy = Literal["none", "api_key", "oauth2", "service_account", "secret_reference"]
SideEffectClass = Literal["none", "read", "write", "destructive"]


@dataclass(frozen=True, slots=True)
class IntegrationOperation:
    resource: str
    action: str
    scope: str
    risk: RiskLevel
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    side_effect: SideEffectClass
    approval_recommended: bool = False


@dataclass(frozen=True, slots=True)
class IntegrationManifest:
    provider: str
    resources: tuple[str, ...]
    capabilities: tuple[str, ...]
    credential_strategy: CredentialStrategy
    operations: tuple[IntegrationOperation, ...]

    def operation(self, action: str) -> IntegrationOperation:
        for operation in self.operations:
            if operation.action == action:
                return operation
        raise KeyError(f"Unknown {self.provider} integration action: {action}")


@dataclass(frozen=True, slots=True)
class IntegrationExecutionResult:
    provider_operation: str
    summary: str
    data: object


class IntegrationAdapter(Protocol):
    provider: str
    manifest: IntegrationManifest

    async def execute(
        self,
        *,
        operation: str,
        configuration: dict[str, str],
        credential: str | None,
        payload: dict[str, object],
    ) -> IntegrationExecutionResult: ...
