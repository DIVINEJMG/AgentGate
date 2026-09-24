from __future__ import annotations

from dataclasses import dataclass

from app.execution.authorization import ProviderPermissionSnapshot
from app.execution.contracts import CapabilityDescriptor, ProviderKind, ResourceDescriptor
from app.execution.providers.registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class AuthorizedCapability:
    capability: CapabilityDescriptor
    resource: ResourceDescriptor
    provider_kind: ProviderKind
    adapter_version: str
    policy_outcome: str

    @property
    def scope(self) -> str:
        return self.capability.scope


class CapabilityResolver:
    """Resolve what a Worker can use without embedding provider-specific logic.

    The resolver consumes normalized inventory produced by integration/provider
    boundaries. Planner callers never ask GitHub, Slack, Google, Browser or MCP
    adapters directly.
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def resolve(
        self,
        *,
        worker_scopes: frozenset[str],
        resources: tuple[ResourceDescriptor, ...],
        permission_snapshots: tuple[ProviderPermissionSnapshot, ...],
        policy_outcomes: dict[tuple[str, str], str],
        suspended_resources: frozenset[str] = frozenset(),
    ) -> tuple[AuthorizedCapability, ...]:
        permissions = {
            (snapshot.provider, snapshot.resource_id): snapshot for snapshot in permission_snapshots
        }
        resolved: list[AuthorizedCapability] = []

        for resource in resources:
            if resource.id in suspended_resources:
                continue
            if resource.health not in {"healthy", "degraded"}:
                continue
            try:
                provider = self._registry.get(resource.provider)
            except KeyError:
                continue

            permission = permissions.get((resource.provider, resource.id))
            if permission is None:
                continue

            for capability in provider.manifest.capabilities:
                if capability.scope not in worker_scopes:
                    continue
                if capability.scope not in resource.available_capabilities:
                    continue
                if not permission.allows(capability.scope):
                    continue

                policy_outcome = policy_outcomes.get(
                    (resource.id, capability.scope),
                    "DENY",
                )
                if policy_outcome == "DENY":
                    continue

                resolved.append(
                    AuthorizedCapability(
                        capability=capability,
                        resource=resource,
                        provider_kind=provider.manifest.kind,
                        adapter_version=provider.manifest.version,
                        policy_outcome=policy_outcome,
                    )
                )

        return tuple(
            sorted(
                resolved,
                key=lambda item: (
                    item.capability.provider,
                    item.resource.id,
                    item.capability.scope,
                ),
            )
        )
