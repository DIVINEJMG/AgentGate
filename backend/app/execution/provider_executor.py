from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.actions.gateway import ActionProposal
from app.domain.integrations.contracts import IntegrationExecutionResult
from app.execution.contracts import (
    ExecutionRequest,
    ProviderRuntimeContext,
    ResourceDescriptor,
)
from app.execution.providers.registry import ProviderRegistry
from app.execution.providers.resolver import ExecutionResolver
from app.infrastructure.database.models import Integration, IntegrationCredential
from app.infrastructure.secrets.integration_crypto import (
    IntegrationCipherError,
    decrypt_integration_secret,
)


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
        config_raw = (
            dict(integration.config)
            if isinstance(integration.config, dict)
            else {}
        )
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
        credential: str | None = None
        if credential_row is not None:
            try:
                credential = decrypt_integration_secret(
                    credential_row.ciphertext
                )
            except IntegrationCipherError as error:
                raise LookupError(
                    "Integration credential is unavailable."
                ) from error

        capabilities = provider.manifest.capabilities
        resource_type = (
            str(config_raw.get("resourceType"))
            if config_raw.get("resourceType")
            else capabilities[0].resource_type
            if capabilities
            else "resource"
        )
        health = (
            "healthy"
            if integration.status == "connected"
            else "degraded"
        )
        resource = ResourceDescriptor(
            id=proposal.resource_id,
            provider=integration.provider,
            resource_type=resource_type,
            external_id=str(
                config_raw.get("resourceKey") or integration.id
            ),
            display_name=integration.display_name,
            metadata=(
                dict(config_raw.get("metadata"))
                if isinstance(config_raw.get("metadata"), dict)
                else {}
            ),
            health=health,
            available_capabilities=tuple(
                capability.scope for capability in capabilities
            ),
            web_url=(
                str(config_raw.get("webUrl"))
                if config_raw.get("webUrl")
                else None
            ),
            configuration=configuration,
        )
        return ProviderRuntimeContext(
            configuration=configuration,
            credential=credential,
            resource=resource,
        )


class UniversalProviderExecutor:
    """Action Gateway executor backed by the F29 provider registry/resolver.

    The gateway still owns authorization. This class only resolves and invokes an
    already-authorized provider capability; it contains no provider-specific branches.
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

    async def execute(
        self,
        proposal: ActionProposal,
    ) -> IntegrationExecutionResult:
        context = await self._context_loader.load(proposal)
        provider = self._registry.get(proposal.provider)
        capability = next(
            (
                item
                for item in provider.manifest.capabilities
                if item.scope == proposal.scope
            ),
            None,
        )
        if capability is None:
            raise LookupError(
                "Connected provider does not expose the requested capability."
            )

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
        resolved = await self._resolver.resolve(
            request,
            contexts={proposal.provider: context},
        )
        result = await resolved.provider.execute(
            request=request,
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
            data=result.output,
        )
