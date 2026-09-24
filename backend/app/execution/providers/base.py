from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.execution.authorization import ProviderPermissionSnapshot
from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionError,
    ExecutionRequest,
    ExecutionResult,
    ProviderHealth,
    ProviderManifest,
    ResourceDescriptor,
    VerificationResult,
)


@runtime_checkable
class ExecutionProvider(Protocol):
    manifest: ProviderManifest

    async def discover_resources(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> tuple[ResourceDescriptor, ...]: ...

    async def discover_capabilities(
        self,
        *,
        resource: ResourceDescriptor | None = None,
    ) -> tuple[CapabilityDescriptor, ...]: ...

    async def check_health(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ProviderHealth: ...

    async def discover_permissions(
        self,
        *,
        resource: ResourceDescriptor,
        configuration: dict[str, str],
        credential: str | None,
        credential_reference: str | None,
    ) -> ProviderPermissionSnapshot: ...

    async def normalize_input(
        self,
        *,
        operation: str,
        input: dict[str, object],
    ) -> dict[str, object]: ...

    async def execute(
        self,
        *,
        request: ExecutionRequest,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ExecutionResult: ...

    async def verify(
        self,
        *,
        request: ExecutionRequest,
        result: ExecutionResult,
        configuration: dict[str, str],
        credential: str | None,
    ) -> VerificationResult: ...


@runtime_checkable
class ManagedExecutionProvider(Protocol):
    """Optional lifecycle surface for providers that own external processes."""

    async def shutdown(self) -> None: ...


@runtime_checkable
class RecoverableExecutionProvider(Protocol):
    """Optional provider-neutral recovery surface for state refresh/replanning."""

    async def recover_execution(
        self,
        *,
        request: ExecutionRequest,
        error: ExecutionError,
        result: ExecutionResult | None,
        configuration: dict[str, str],
        credential: str | None,
    ) -> str | None: ...
