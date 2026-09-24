from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.execution.contracts import CredentialStrategy, ExecutionRequest


@dataclass(frozen=True, slots=True)
class CredentialReference:
    """Opaque credential handle. Raw secrets never belong in runtime/planner payloads."""

    strategy: CredentialStrategy
    reference: str | None

    def __post_init__(self) -> None:
        if self.strategy == "none" and self.reference is not None:
            raise ValueError("Credential strategy 'none' cannot carry a reference.")
        if self.reference is not None and not self.reference.startswith("secret://"):
            raise ValueError("Credential references must use the secret:// scheme.")


@dataclass(frozen=True, slots=True)
class ProviderPermissionSnapshot:
    provider: str
    resource_id: str
    capability_scopes: tuple[str, ...]
    credential: CredentialReference
    checked_at: datetime
    adapter: str = "native_api"
    adapter_version: str = "unknown"
    source: str = "provider"
    metadata: dict[str, object] = field(default_factory=dict)

    def allows(self, scope: str) -> bool:
        return scope in self.capability_scopes


@dataclass(frozen=True, slots=True)
class ExecutionAuthorizationSnapshot:
    organization_id: UUID
    agent_id: UUID
    worker_id: UUID | None
    capability_scope: str
    resource_id: str
    resource_external_id: str
    resource_origin: str | None
    provider: str
    provider_permissions: tuple[str, ...]
    risk: str
    policy_outcome: str
    policy_reason: str
    approval_id: str | None
    approval_status: str | None
    approval_required: bool
    approval_recommendation: str
    action_fingerprint: str
    approval_action_fingerprint: str | None
    adapter: str
    adapter_version: str
    credential_strategy: str
    credential_reference: str | None
    correlation_id: str
    authorized_at: datetime

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["organization_id"] = str(self.organization_id)
        value["agent_id"] = str(self.agent_id)
        value["worker_id"] = str(self.worker_id) if self.worker_id else None
        value["authorized_at"] = self.authorized_at.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class UniversalActionRequest:
    execution: ExecutionRequest
    provider_permissions: ProviderPermissionSnapshot
    approval_id: str | None = None
    approval_status: str | None = None
    approval_action_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.provider_permissions.provider != self.execution.resource.provider:
            raise ValueError("Provider permission snapshot belongs to another provider.")
        if self.provider_permissions.resource_id != self.execution.resource.id:
            raise ValueError("Provider permission snapshot belongs to another resource.")


def action_fingerprint(request: UniversalActionRequest) -> str:
    execution = request.execution
    canonical = {
        "organization_id": str(execution.organization_id),
        "agent_id": str(execution.agent_id),
        "worker_id": str(execution.worker_id) if execution.worker_id else None,
        "capability": execution.capability.scope,
        "resource_id": execution.resource.id,
        "resource_external_id": execution.resource.external_id,
        "operation": execution.operation,
        "input": execution.input,
        "idempotency_key": execution.idempotency_key,
    }
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _resource_origin(request: UniversalActionRequest) -> str | None:
    resource = request.execution.resource
    metadata_origin = resource.metadata.get("origin")
    if isinstance(metadata_origin, str) and metadata_origin:
        return metadata_origin
    if resource.external_id.startswith("origin:"):
        return resource.external_id.removeprefix("origin:")
    if resource.web_url:
        from urllib.parse import urlsplit

        parts = urlsplit(resource.web_url)
        if parts.scheme and parts.hostname:
            port = f":{parts.port}" if parts.port is not None else ""
            return f"{parts.scheme}://{parts.hostname}{port}"
    return None


def build_authorization_snapshot(
    *,
    request: UniversalActionRequest,
    risk: str,
    policy_outcome: str,
    policy_reason: str,
    adapter: str,
    adapter_version: str,
) -> ExecutionAuthorizationSnapshot:
    execution = request.execution
    return ExecutionAuthorizationSnapshot(
        organization_id=execution.organization_id,
        agent_id=execution.agent_id,
        worker_id=execution.worker_id,
        capability_scope=execution.capability.scope,
        resource_id=execution.resource.id,
        resource_external_id=execution.resource.external_id,
        resource_origin=_resource_origin(request),
        provider=execution.resource.provider,
        provider_permissions=request.provider_permissions.capability_scopes,
        risk=risk,
        policy_outcome=policy_outcome,
        policy_reason=policy_reason,
        approval_id=request.approval_id,
        approval_status=request.approval_status,
        approval_required=policy_outcome == "REQUIRE_APPROVAL",
        approval_recommendation=execution.capability.approval_recommendation,
        action_fingerprint=action_fingerprint(request),
        approval_action_fingerprint=request.approval_action_fingerprint,
        adapter=adapter,
        adapter_version=adapter_version,
        credential_strategy=request.provider_permissions.credential.strategy,
        credential_reference=request.provider_permissions.credential.reference,
        correlation_id=execution.correlation_id,
        authorized_at=datetime.now(UTC),
    )
