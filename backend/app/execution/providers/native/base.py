from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from app.execution.authorization import CredentialReference, ProviderPermissionSnapshot
from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionProviderError,
    ExecutionRequest,
    ExecutionResult,
    ProviderHealth,
    ProviderManifest,
    ResourceDescriptor,
    VerificationResult,
)
from app.execution.providers.native.http import (
    ProviderHTTPClient,
    ProviderTransportError,
)


class NativeProvider(ABC):
    manifest: ProviderManifest

    def __init__(self, http: ProviderHTTPClient | None = None) -> None:
        self._http = http or ProviderHTTPClient()

    async def discover_capabilities(
        self,
        *,
        resource: ResourceDescriptor | None = None,
    ) -> tuple[CapabilityDescriptor, ...]:
        if resource is not None and resource.provider != self.manifest.provider:
            return ()
        return self.manifest.capabilities

    async def discover_permissions(
        self,
        *,
        resource: ResourceDescriptor,
        configuration: dict[str, str],
        credential: str | None,
        credential_reference: str | None,
    ) -> ProviderPermissionSnapshot:
        if resource.provider != self.manifest.provider:
            raise ValueError("Permission discovery resource belongs to another provider.")

        declared = set(resource.available_capabilities)
        configured_raw = configuration.get("permissionScopes", "").strip()
        configured = {item.strip() for item in configured_raw.split(",") if item.strip()}
        if configured:
            declared &= configured

        allowed: list[str] = []
        for capability in self.manifest.capabilities:
            if capability.scope not in declared:
                continue
            if capability.requires_credential and not credential:
                continue
            allowed.append(capability.scope)

        strategy = self.manifest.credential_strategy
        reference = credential_reference if strategy != "none" else None
        return ProviderPermissionSnapshot(
            provider=self.manifest.provider,
            resource_id=resource.id,
            capability_scopes=tuple(sorted(allowed)),
            credential=CredentialReference(
                strategy=strategy,
                reference=reference,
            ),
            checked_at=datetime.now(UTC),
            adapter=self.manifest.kind,
            adapter_version=self.manifest.version,
            metadata={"configured_scope_filter": bool(configured)},
        )

    async def normalize_input(
        self,
        *,
        operation: str,
        input: dict[str, object],
    ) -> dict[str, object]:
        if input:
            raise self._execution_error(
                operation=operation,
                correlation_id="input-validation",
                code="validation_error",
                retryable=False,
                safe_message="This provider operation does not accept an input payload.",
            )
        return {}

    async def verify(
        self,
        *,
        request: ExecutionRequest,
        result: ExecutionResult,
        configuration: dict[str, str],
        credential: str | None,
    ) -> VerificationResult:
        del configuration, credential
        return VerificationResult(
            verified=result.status == "success",
            summary=(
                "Provider returned a successful canonical response."
                if result.status == "success"
                else "Provider execution was not successful."
            ),
            details={
                "provider": self.manifest.provider,
                "operation": request.operation,
            },
        )

    def _execution_error(
        self,
        *,
        operation: str,
        correlation_id: str,
        code,
        retryable: bool,
        safe_message: str,
        internal_details: str | None = None,
    ) -> ExecutionProviderError:
        return ExecutionProviderError(
            code=code,
            retryable=retryable,
            provider=self.manifest.provider,
            operation=operation,
            correlation_id=correlation_id,
            safe_message=safe_message,
            internal_details=internal_details,
        )

    def _transport_error(
        self,
        error: ProviderTransportError,
        *,
        operation: str,
        correlation_id: str,
    ) -> ExecutionProviderError:
        return self._execution_error(
            operation=operation,
            correlation_id=correlation_id,
            code=error.code,
            retryable=error.retryable,
            safe_message=error.safe_message,
            internal_details=error.internal_details,
        )

    def _result(
        self,
        *,
        request: ExecutionRequest,
        output: object,
        provider_request_id: str | None,
        started_at: datetime,
        verification: VerificationResult | None = None,
    ) -> ExecutionResult:
        return ExecutionResult.successful(
            provider=self.manifest.provider,
            adapter=self.manifest.kind,
            adapter_version=self.manifest.version,
            operation=request.operation,
            output=output,
            provider_request_id=provider_request_id,
            started_at=started_at,
            verification=verification,
        )

    @abstractmethod
    async def discover_resources(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> tuple[ResourceDescriptor, ...]: ...

    @abstractmethod
    async def check_health(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ProviderHealth: ...

    @abstractmethod
    async def execute(
        self,
        *,
        request: ExecutionRequest,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ExecutionResult: ...


def utcnow() -> datetime:
    return datetime.now(UTC)


def mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def text(value: object) -> str:
    return value if isinstance(value, str) else ""


def number(value: object) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
