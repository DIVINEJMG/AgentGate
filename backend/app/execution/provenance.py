from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionProvenance:
    provider: str | None
    capability: str
    resource_id: str
    adapter: str | None
    adapter_version: str | None
    action_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "provider": self.provider,
            "capability": self.capability,
            "resourceId": self.resource_id,
            "adapter": self.adapter,
            "adapterVersion": self.adapter_version,
            "actionId": self.action_id,
        }


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def provenance_from_action_payload(
    *,
    scope: str,
    resource_id: str,
    payload: Mapping[str, object],
    action_id: str | None = None,
) -> ExecutionProvenance:
    request = _mapping(payload.get("request"))
    authorization = _mapping(payload.get("authorization"))
    result = _mapping(payload.get("result"))
    result_data = _mapping(result.get("data"))
    result_authorization = _mapping(result_data.get("authorization"))

    provider = (
        authorization.get("provider")
        or result_authorization.get("provider")
        or request.get("provider")
    )
    adapter = (
        authorization.get("adapter")
        or result_authorization.get("adapter")
        or request.get("adapter")
    )
    adapter_version = (
        authorization.get("adapter_version")
        or authorization.get("adapterVersion")
        or result_authorization.get("adapter_version")
        or result_authorization.get("adapterVersion")
        or request.get("adapterVersion")
    )
    capability = (
        authorization.get("capability_scope")
        or authorization.get("capabilityScope")
        or request.get("scope")
        or scope
    )
    bound_resource = (
        authorization.get("resource_id")
        or authorization.get("resourceId")
        or request.get("resourceId")
        or resource_id
    )

    return ExecutionProvenance(
        provider=str(provider) if provider else None,
        capability=str(capability),
        resource_id=str(bound_resource),
        adapter=str(adapter) if adapter else None,
        adapter_version=str(adapter_version) if adapter_version else None,
        action_id=action_id,
    )


def audit_provenance_payload(
    *,
    scope: str,
    resource_id: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    provenance = provenance_from_action_payload(
        scope=scope,
        resource_id=resource_id,
        payload=payload,
    )
    return {
        "provider": provenance.provider,
        "capability": provenance.capability,
        "resource_id": provenance.resource_id,
        "adapter": provenance.adapter,
        "adapter_version": provenance.adapter_version,
    }
