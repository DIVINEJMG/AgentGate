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
    """Backward-compatible operation manifest used by existing adapters."""

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
    credential_strategy: CredentialStrategy = "secret_reference"

    @property
    def operation(self) -> IntegrationOperation:
        return IntegrationOperation(
            resource=self.resource,
            action=self.action,
            scope=self.scope,
            risk=self.risk,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            side_effect="write" if self.side_effect else "read",
            approval_recommended=self.approval_default,
        )


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    provider: str
    resources: tuple[str, ...]
    capabilities: tuple[str, ...]
    credential_strategy: CredentialStrategy
    operations: tuple[IntegrationOperation, ...]


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


def provider_manifest(adapter: IntegrationAdapter) -> ProviderManifest:
    manifests = adapter.manifests
    if not manifests:
        return ProviderManifest(
            provider=adapter.provider,
            resources=(),
            capabilities=(),
            credential_strategy="none",
            operations=(),
        )
    strategies = {manifest.credential_strategy for manifest in manifests}
    if len(strategies) != 1:
        raise ValueError(f"{adapter.provider} manifests disagree on credential strategy.")
    return ProviderManifest(
        provider=adapter.provider,
        resources=tuple(dict.fromkeys(manifest.resource for manifest in manifests)),
        capabilities=tuple(dict.fromkeys(manifest.capability for manifest in manifests)),
        credential_strategy=next(iter(strategies)),
        operations=tuple(manifest.operation for manifest in manifests),
    )
