from __future__ import annotations

from dataclasses import dataclass

from audoryn_shared.execution.authorization import ProviderPermissionSnapshot
from audoryn_shared.execution.capability_resolver import AuthorizedCapability, CapabilityResolver
from audoryn_shared.execution.contracts import ResourceDescriptor


@dataclass(frozen=True, slots=True)
class PlannerCapabilityQuery:
    """Provider-neutral Planner query boundary."""

    resolver: CapabilityResolver

    def authorized(
        self,
        *,
        worker_scopes: frozenset[str],
        resources: tuple[ResourceDescriptor, ...],
        permission_snapshots: tuple[ProviderPermissionSnapshot, ...],
        policy_outcomes: dict[tuple[str, str], str],
        suspended_resources: frozenset[str] = frozenset(),
    ) -> tuple[AuthorizedCapability, ...]:
        return self.resolver.resolve(
            worker_scopes=worker_scopes,
            resources=resources,
            permission_snapshots=permission_snapshots,
            policy_outcomes=policy_outcomes,
            suspended_resources=suspended_resources,
        )
