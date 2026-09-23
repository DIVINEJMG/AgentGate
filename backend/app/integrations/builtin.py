from __future__ import annotations

from uuid import UUID

from app.domain.integrations.contracts import (
    CredentialStrategy,
    IntegrationExecutionResult,
    IntegrationManifest,
)
from app.execution.bootstrap import execution_provider_registry
from app.execution.contracts import ExecutionRequest, ResourceDescriptor
from app.execution.providers.base import ExecutionProvider


def _legacy_credential_strategy(provider: ExecutionProvider) -> CredentialStrategy:
    strategy = provider.manifest.credential_strategy
    if strategy == "none":
        return "none"
    if strategy in {"oauth_access_token", "oauth_refresh_token"}:
        return "oauth2"
    if strategy == "service_account":
        return "service_account"
    if strategy == "api_token":
        return "api_key"
    return "secret_reference"


def _legacy_manifests(
    provider: ExecutionProvider,
) -> tuple[IntegrationManifest, ...]:
    strategy = _legacy_credential_strategy(provider)
    return tuple(
        IntegrationManifest(
            provider=provider.manifest.provider,
            resource=capability.resource_type,
            capability=capability.scope,
            action=capability.operation,
            scope=capability.scope,
            risk=capability.risk,
            input_schema=capability.input_schema,
            output_schema=capability.output_schema,
            side_effect=capability.side_effect,
            approval_default=capability.approval_recommendation == "required",
            credential_strategy=strategy,
        )
        for capability in provider.manifest.capabilities
    )


class UniversalIntegrationAdapter:
    """Compatibility facade for pre-F29 integration consumers.

    New code should consume app.execution directly. This wrapper prevents legacy
    imports from becoming a second provider implementation.
    """

    def __init__(self, provider: ExecutionProvider) -> None:
        self._provider = provider
        self.provider = provider.manifest.provider
        self.manifests = _legacy_manifests(provider)

    async def execute(
        self,
        *,
        operation: str,
        configuration: dict[str, str],
        credential: str | None,
        payload: dict[str, object],
    ) -> IntegrationExecutionResult:
        capability = next(
            (
                item
                for item in self._provider.manifest.capabilities
                if item.operation == operation
                or item.scope == operation
            ),
            None,
        )
        if capability is None:
            raise RuntimeError(
                f"{self.provider}:{operation} is not a registered capability."
            )
        normalized = await self._provider.normalize_input(
            operation=capability.operation,
            input=payload,
        )
        resource_key = (
            configuration.get("resourceKey")
            or configuration.get("repository")
            or configuration.get("account")
            or configuration.get("teamId")
            or configuration.get("calendarId")
            or self.provider
        )
        resource = ResourceDescriptor(
            id=f"legacy:{self.provider}:{resource_key}",
            provider=self.provider,
            resource_type=capability.resource_type,
            external_id=resource_key,
            display_name=resource_key,
            metadata={},
            health="healthy",
            available_capabilities=tuple(
                item.scope for item in self._provider.manifest.capabilities
            ),
            configuration=configuration,
        )
        request = ExecutionRequest(
            organization_id=UUID(int=0),
            worker_id=None,
            agent_id=UUID(int=0),
            job_id=None,
            work_item_id=None,
            run_id=None,
            capability=capability,
            resource=resource,
            operation=capability.operation,
            input=normalized,
            correlation_id="legacy-integration-adapter",
            idempotency_key=f"legacy:{self.provider}:{capability.operation}",
        )
        result = await self._provider.execute(
            request=request,
            configuration=configuration,
            credential=credential,
        )
        return IntegrationExecutionResult(
            provider_operation=result.operation,
            summary=(
                result.verification.summary
                if result.verification is not None
                else f"{self.provider}:{result.operation} executed."
            ),
            data=result.output,
        )


_registry = execution_provider_registry()
github_adapter = UniversalIntegrationAdapter(_registry.get("github"))
gmail_adapter = UniversalIntegrationAdapter(_registry.get("gmail"))
slack_adapter = UniversalIntegrationAdapter(_registry.get("slack"))
drive_adapter = UniversalIntegrationAdapter(_registry.get("google_drive"))
calendar_adapter = UniversalIntegrationAdapter(_registry.get("google_calendar"))
