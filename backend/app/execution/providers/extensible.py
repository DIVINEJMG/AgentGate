from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from app.execution.contracts import CapabilityDescriptor, ResourceDescriptor
from app.execution.providers.base import ExecutionProvider

ExtensibleProviderType = Literal[
    "mcp",
    "openapi",
    "internal_api",
    "company_tool",
    "developer_plugin",
]


@dataclass(frozen=True, slots=True)
class ExternalToolDescriptor:
    provider_type: ExtensibleProviderType
    provider_id: str
    tool_id: str
    display_name: str
    capabilities: tuple[CapabilityDescriptor, ...]
    resources: tuple[ResourceDescriptor, ...]
    metadata: dict[str, object]


@runtime_checkable
class CustomExecutionProvider(ExecutionProvider, Protocol):
    """Discovery contract for MCP/OpenAPI/internal/custom tools.

    Discovered tools must be normalized into F29 capability/resource descriptors
    before they can reach the Action Gateway and universal execution path.
    """

    provider_type: ExtensibleProviderType

    async def discover_tools(self) -> tuple[ExternalToolDescriptor, ...]: ...

    async def normalize_tool(
        self,
        descriptor: ExternalToolDescriptor,
    ) -> ExternalToolDescriptor: ...


@runtime_checkable
class MCPExecutionProvider(CustomExecutionProvider, Protocol):
    provider_type: Literal["mcp"]

    async def connect(self) -> None: ...

    async def close(self) -> None: ...
