from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    VerificationResult,
)
from app.execution.providers.base import ExecutionProvider


@runtime_checkable
class BrowserExecutionProvider(ExecutionProvider, Protocol):
    """Interface only. F30 supplies the browser engine implementation.

    Browser execution remains a universal ExecutionProvider and is therefore
    selected only after the same ActionGateway governance used by native adapters.
    """

    async def open_session(self, request: ExecutionRequest) -> object: ...

    async def discover_page_capabilities(
        self,
        *,
        session: object,
        request: ExecutionRequest,
    ) -> tuple[CapabilityDescriptor, ...]: ...

    async def observe(
        self,
        *,
        session: object,
        request: ExecutionRequest,
    ) -> dict[str, object]: ...

    async def execute(
        self,
        *,
        request: ExecutionRequest,
        configuration: dict[str, str],
        credential: str | None,
        session: object | None = None,
    ) -> ExecutionResult: ...

    async def verify(
        self,
        *,
        request: ExecutionRequest,
        result: ExecutionResult,
        configuration: dict[str, str],
        credential: str | None,
        session: object | None = None,
        expected_state: dict[str, object] | None = None,
    ) -> VerificationResult: ...

    async def recover(
        self,
        *,
        session: object,
        request: ExecutionRequest,
        result: ExecutionResult | None,
        observation: dict[str, object],
    ) -> dict[str, object]: ...
