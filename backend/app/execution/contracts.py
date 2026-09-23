from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

RiskLevel = Literal["low", "medium", "high", "critical"]
ExecutionStatus = Literal[
    "success",
    "failed",
    "blocked",
    "waiting_approval",
    "retryable",
    "cancelled",
]
ExecutionErrorCode = Literal[
    "authentication_error",
    "authorization_error",
    "resource_not_found",
    "rate_limited",
    "temporary_provider_error",
    "validation_error",
    "policy_blocked",
    "approval_required",
    "verification_failed",
    "timeout",
    "provider_unavailable",
    "unsupported_operation",
]
ProviderKind = Literal["native_api", "browser", "mcp"]
CapabilityMode = Literal["read", "write", "action"]
ApprovalRecommendation = Literal["none", "recommended", "required"]
CredentialStrategy = Literal[
    "none",
    "api_token",
    "oauth_access_token",
    "oauth_refresh_token",
    "session",
    "service_account",
    "agent_managed_session",
    "secret_reference",
]
ResourceHealth = Literal["healthy", "degraded", "unavailable", "unknown"]


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    scope: str
    provider: str
    resource_type: str
    operation: str
    mode: CapabilityMode
    risk: RiskLevel
    requires_credential: bool
    side_effect: bool
    approval_recommendation: ApprovalRecommendation
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    description: str
    target: str

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise ValueError("Capability scope is required.")
        if not self.provider.strip():
            raise ValueError("Capability provider is required.")
        if not self.resource_type.strip():
            raise ValueError("Capability resource_type is required.")
        if not self.operation.strip():
            raise ValueError("Capability operation is required.")
        if not self.target.strip():
            raise ValueError("Capability target is required.")
        if self.side_effect and self.mode == "read":
            raise ValueError("Read capabilities cannot declare side effects.")


@dataclass(frozen=True, slots=True)
class ResourceDescriptor:
    id: str
    provider: str
    resource_type: str
    external_id: str
    display_name: str
    metadata: dict[str, object]
    health: ResourceHealth
    available_capabilities: tuple[str, ...]
    web_url: str | None = None
    configuration: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Resource id is required.")
        if not self.external_id.strip():
            raise ValueError("Resource external_id is required.")


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    state: ResourceHealth
    message: str
    checked_at: datetime
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    provider: str
    display_name: str
    kind: ProviderKind
    version: str
    credential_strategy: CredentialStrategy
    capabilities: tuple[CapabilityDescriptor, ...]
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("Provider id is required.")
        if not self.version.strip():
            raise ValueError("Provider version is required.")
        scopes = [capability.scope for capability in self.capabilities]
        operations = [capability.operation for capability in self.capabilities]
        if len(scopes) != len(set(scopes)):
            raise ValueError(f"{self.provider} contains duplicate capability scopes.")
        if len(operations) != len(set(operations)):
            raise ValueError(f"{self.provider} contains duplicate operations.")


@dataclass(frozen=True, slots=True)
class ExecutionPreferences:
    preferred_provider_kinds: tuple[ProviderKind, ...] = (
        "native_api",
        "browser",
        "mcp",
    )
    allow_fallback: bool = True
    required_adapter_version: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    organization_id: UUID
    worker_id: UUID | None
    agent_id: UUID
    job_id: UUID | None
    work_item_id: UUID | None
    run_id: UUID | None
    capability: CapabilityDescriptor
    resource: ResourceDescriptor
    operation: str
    input: dict[str, object]
    correlation_id: str
    idempotency_key: str
    execution_preferences: ExecutionPreferences = field(
        default_factory=ExecutionPreferences
    )

    def __post_init__(self) -> None:
        if not self.correlation_id.strip():
            raise ValueError("Execution correlation_id is required.")
        if not self.idempotency_key.strip():
            raise ValueError("Execution idempotency_key is required.")
        if self.capability.provider != self.resource.provider:
            raise ValueError("Capability and resource providers must match.")
        if self.operation != self.capability.operation:
            raise ValueError("Execution operation must match the capability operation.")
        if self.capability.scope not in self.resource.available_capabilities:
            raise ValueError("Resource does not expose the requested capability.")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verified: bool
    summary: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionError:
    code: ExecutionErrorCode
    retryable: bool
    provider: str
    operation: str
    correlation_id: str
    safe_message: str
    internal_details: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: ExecutionStatus
    provider: str
    adapter: ProviderKind
    adapter_version: str
    operation: str
    output: object
    verification: VerificationResult | None
    artifacts: tuple[str, ...]
    provider_request_id: str | None
    started_at: datetime
    completed_at: datetime
    latency_ms: int
    error: ExecutionError | None = None

    @classmethod
    def successful(
        cls,
        *,
        provider: str,
        adapter: ProviderKind,
        adapter_version: str,
        operation: str,
        output: object,
        provider_request_id: str | None,
        started_at: datetime,
        verification: VerificationResult | None = None,
        artifacts: tuple[str, ...] = (),
    ) -> "ExecutionResult":
        completed_at = datetime.now(UTC)
        latency_ms = max(
            0,
            int((completed_at - started_at).total_seconds() * 1000),
        )
        return cls(
            status="success",
            provider=provider,
            adapter=adapter,
            adapter_version=adapter_version,
            operation=operation,
            output=output,
            verification=verification,
            artifacts=artifacts,
            provider_request_id=provider_request_id,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=latency_ms,
        )


class ExecutionProviderError(RuntimeError):
    def __init__(
        self,
        *,
        code: ExecutionErrorCode,
        retryable: bool,
        provider: str,
        operation: str,
        correlation_id: str,
        safe_message: str,
        internal_details: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.error = ExecutionError(
            code=code,
            retryable=retryable,
            provider=provider,
            operation=operation,
            correlation_id=correlation_id,
            safe_message=safe_message,
            internal_details=internal_details,
        )


@dataclass(frozen=True, slots=True)
class ProviderRuntimeContext:
    configuration: dict[str, str]
    credential: str | None
    resource: ResourceDescriptor
