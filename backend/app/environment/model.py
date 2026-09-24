from __future__ import annotations

from dataclasses import dataclass, field

from app.execution.authorization import CredentialReference
from app.execution.contracts import CapabilityDescriptor, ResourceDescriptor


@dataclass(frozen=True, slots=True)
class EnvironmentSystem:
    id: str
    provider: str
    display_name: str
    resources: tuple[ResourceDescriptor, ...]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResourceRelationship:
    source_resource_id: str
    target_resource_id: str
    relationship: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    systems: tuple[EnvironmentSystem, ...]
    relationships: tuple[ResourceRelationship, ...]
    capabilities: tuple[CapabilityDescriptor, ...]
    credentials: tuple[CredentialReference, ...]
    owners: tuple[str, ...]
    metadata: dict[str, object] = field(default_factory=dict)

    def resource_index(self) -> dict[str, ResourceDescriptor]:
        return {
            resource.id: resource
            for system in self.systems
            for resource in system.resources
        }

    def capability_index(self) -> dict[str, CapabilityDescriptor]:
        return {capability.scope: capability for capability in self.capabilities}
