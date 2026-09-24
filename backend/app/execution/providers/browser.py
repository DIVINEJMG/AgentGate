from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    VerificationResult,
)


@runtime_checkable
class BrowserExecutionProvider(Protocol):
    """Interface only. F30 supplies the browser engine implementation.

    Browser execution is still invoked only after ActionGateway authorization; this
    protocol intentionally contains no policy or credential bypass.
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
        session: object,
        request: ExecutionRequest,
    ) -> ExecutionResult: ...

    async def verify(
        self,
        *,
        session: object,
        request: ExecutionRequest,
        result: ExecutionResult,
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
