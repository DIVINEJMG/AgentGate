from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.actions.gateway import ActionProposal
from app.domain.integrations.contracts import IntegrationExecutionResult
from app.execution.authorization import (
    ExecutionAuthorizationSnapshot,
    UniversalActionRequest,
)
from app.execution.contracts import (
    ExecutionRequest,
    ProviderRuntimeContext,
    ResourceDescriptor,
)
from app.execution.providers.registry import ProviderRegistry
from app.execution.providers.resolver import ExecutionResolver
from app.infrastructure.database.models import Integration, IntegrationCredential
from app.infrastructure.secrets.provider_vault import DatabaseSecretVault


class ProviderContextLoader(Protocol):
    async def load(self, proposal: ActionProposal) -> ProviderRuntimeContext: ...


class DatabaseProviderContextLoader:
    def __init__(
        self,
        session: AsyncSession,
        registry: ProviderRegistry,
    ) -> None:
        self._session = session
        self._registry = registry
        self._vault = DatabaseSecretVault(session)

    async def load(self, proposal: ActionProposal) -> ProviderRuntimeContext:
        raw_id = proposal.resource_id
        if raw_id.startswith("integration:"):
            raw_id = raw_id.split(":", 1)[1]
        try:
            integration_id = UUID(raw_id)
        except ValueError as error:
            raise LookupError("Action resource is not a canonical integration resource.") from error

        integration = await self._session.scalar(
            select(Integration).where(
                Integration.organization_id == proposal.organization_id,
                Integration.id == integration_id,
            )
        )
        if integration is None or integration.status != "connected":
            raise LookupError("Integration is not in a connected execution state.")
        if integration.provider != proposal.provider:
            raise LookupError("Action provider does not match the connected resource.")

        provider = self._registry.get(integration.provider)
        config_raw = dict(integration.config) if isinstance(integration.config, dict) else {}
        configuration = {
            str(key): str(value)
            for key, value in config_raw.items()
            if isinstance(value, (str, int, float, bool))
        }

        credential_row = await self._session.scalar(
            select(IntegrationCredential).where(
                IntegrationCredential.integration_id == integration.id
            )
        )
        credential_reference: str | None = None
        credential: str | None = None
        if credential_row is not None:
            credential_reference = self._vault.reference_for(credential_row.id)
            # Raw secret resolution happens only here, at the provider edge.
            credential = await self._vault.read(credential_reference)

        capabilities = provider.manifest.capabilities
        resource_type = (
            str(config_raw.get("resourceType"))
            if config_raw.get("resourceType")
            else capabilities[0].resource_type
            if capabilities
            else "resource"
        )
        health = "healthy" if integration.status == "connected" else "degraded"
        raw_metadata = config_raw.get("metadata")
        metadata: dict[str, object] = (
            {str(key): value for key, value in raw_metadata.items()}
            if isinstance(raw_metadata, dict)
            else {}
        )
        resource = ResourceDescriptor(
            id=proposal.resource_id,
            provider=integration.provider,
            resource_type=resource_type,
            external_id=str(config_raw.get("resourceKey") or integration.id),
            display_name=integration.display_name,
            metadata=metadata,
            health=health,
            available_capabilities=tuple(capability.scope for capability in capabilities),
            web_url=(str(config_raw.get("webUrl")) if config_raw.get("webUrl") else None),
            configuration=configuration,
        )
        return ProviderRuntimeContext(
            configuration=configuration,
            credential=credential,
            resource=resource,
            credential_reference=credential_reference,
        )


class UniversalProviderExecutor:
    """Provider-neutral executor behind the Action Gateway.

    Authorization is completed before execute_request is called. Provider-specific
    behavior remains inside the registered adapters.
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        context_loader: ProviderContextLoader,
    ) -> None:
        self._registry = registry
        self._resolver = ExecutionResolver(registry)
        self._context_loader = context_loader

    async def prepare(self, proposal: ActionProposal) -> UniversalActionRequest:
        context = await self._context_loader.load(proposal)
        provider = self._registry.get(proposal.provider)
        capability = next(
            (item for item in provider.manifest.capabilities if item.scope == proposal.scope),
            None,
        )
        if capability is None:
            raise LookupError("Connected provider does not expose the requested capability.")

        normalized_input = await provider.normalize_input(
            operation=capability.operation,
            input=proposal.payload,
        )
        request = ExecutionRequest(
            organization_id=proposal.organization_id,
            worker_id=None,
            agent_id=proposal.agent_id,
            job_id=None,
            work_item_id=None,
            run_id=None,
            capability=capability,
            resource=context.resource,
            operation=capability.operation,
            input=normalized_input,
            correlation_id=proposal.correlation_id,
            idempotency_key=proposal.idempotency_key,
        )
        permissions = await provider.discover_permissions(
            resource=context.resource,
            configuration=context.configuration,
            credential=context.credential,
            credential_reference=context.credential_reference,
        )
        return UniversalActionRequest(
            execution=request,
            provider_permissions=permissions,
        )

    async def execute_request(
        self,
        request: UniversalActionRequest,
        snapshot: ExecutionAuthorizationSnapshot,
    ) -> IntegrationExecutionResult:
        execution = request.execution
        if snapshot.capability_scope != execution.capability.scope:
            raise PermissionError("Authorization snapshot capability mismatch.")
        if snapshot.resource_id != execution.resource.id:
            raise PermissionError("Authorization snapshot resource mismatch.")
        if snapshot.correlation_id != execution.correlation_id:
            raise PermissionError("Authorization snapshot correlation mismatch.")

        proposal = ActionProposal(
            organization_id=execution.organization_id,
            agent_id=execution.agent_id,
            provider=execution.resource.provider,
            operation=execution.operation,
            scope=execution.capability.scope,
            resource_id=execution.resource.id,
            payload=execution.input,
            correlation_id=execution.correlation_id,
            idempotency_key=execution.idempotency_key,
        )
        context = await self._context_loader.load(proposal)
        resolved = await self._resolver.resolve(
            execution,
            contexts={execution.resource.provider: context},
        )
        result = await resolved.provider.execute(
            request=execution,
            configuration=resolved.context.configuration,
            credential=resolved.context.credential,
        )
        summary = (
            result.verification.summary
            if result.verification is not None
            else f"{result.provider}:{result.operation} executed."
        )
        return IntegrationExecutionResult(
            provider_operation=result.operation,
            summary=summary,
            data={
                "output": result.output,
                "authorization": snapshot.as_dict(),
            },
        )

    async def execute(
        self,
        proposal: ActionProposal,
    ) -> IntegrationExecutionResult:
        """Compatibility path for direct pre-F29 callers.

        The Action Gateway should call prepare + execute_request so authorization
        snapshots are always present on real provider execution.
        """
        prepared = await self.prepare(proposal)
        raise PermissionError(
            "Direct provider execution is disabled; execute through ActionGateway."
        )
