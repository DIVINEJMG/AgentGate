from __future__ import annotations

from dataclasses import dataclass

from app.execution.contracts import (
    ExecutionRequest,
    ProviderRuntimeContext,
)
from app.execution.providers.base import ExecutionProvider
from app.execution.providers.registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class ResolvedExecutionProvider:
    provider: ExecutionProvider
    context: ProviderRuntimeContext


class ExecutionResolver:
    """Resolve execution without embedding provider-specific branches.

    F29 keeps the selection deterministic:
    1. respect the request's provider-kind preference;
    2. require an enabled adapter exposing the exact scope + operation;
    3. require the resource to belong to the provider and be healthy enough;
    4. require credentials when the capability says they are needed.
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def resolve(
        self,
        request: ExecutionRequest,
        *,
        contexts: dict[str, ProviderRuntimeContext],
    ) -> ResolvedExecutionProvider:
        preference = request.execution_preferences
        candidates = self._registry.capability_providers(request.capability.scope)

        for kind in preference.preferred_provider_kinds:
            for provider in candidates:
                manifest = provider.manifest
                if manifest.kind != kind:
                    continue
                if (
                    preference.required_adapter_version is not None
                    and manifest.version != preference.required_adapter_version
                ):
                    continue
                if request.resource.provider != manifest.provider:
                    continue
                context = contexts.get(manifest.provider)
                if context is None:
                    continue
                if context.resource.id != request.resource.id:
                    continue
                if context.resource.health not in {"healthy", "degraded"}:
                    continue
                capability = next(
                    (
                        item
                        for item in manifest.capabilities
                        if item.scope == request.capability.scope
                        and item.operation == request.operation
                    ),
                    None,
                )
                if capability is None:
                    continue
                if capability.requires_credential and not context.credential:
                    continue
                health = await provider.check_health(
                    configuration=context.configuration,
                    credential=context.credential,
                )
                if health.state != "healthy":
                    continue
                return ResolvedExecutionProvider(
                    provider=provider,
                    context=context,
                )
            if not preference.allow_fallback:
                break

        raise LookupError("No healthy execution provider can satisfy the requested capability.")
