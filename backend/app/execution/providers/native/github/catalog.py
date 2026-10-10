"""The single source of truth for typed, bounded GitHub actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.execution.contracts import CapabilityDescriptor

STRING = {"type": "string", "minLength": 1, "maxLength": 1000}
BODY = {"type": "string", "maxLength": 10000}
NUMBER = {"type": "integer", "minimum": 1}
SHA = {"type": "string", "pattern": "^[a-fA-F0-9]{40}$"}
PAGE = {"type": "integer", "minimum": 1, "maximum": 100}
STRINGS = {"type": "array", "maxItems": 20, "items": STRING}
ExecutionSemantics = Literal[
    "api",
    "inspection",
    "initialization",
    "workspace_mutation",
    "command_launch",
    "command_cancel",
    "workspace_close",
]


@dataclass(frozen=True)
class GitHubAction:
    operation: str
    description: str
    permission: str
    method: str = "GET"
    path: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    query: tuple[str, ...] = ()
    approval: bool = False
    resource_type: str = "repository"
    graphql: str = ""
    scope_alias: str = ""
    verification: str = "canonical_read"
    reconciliation: str = "none"
    prerequisites: tuple[str, ...] = ()
    execution_semantics: ExecutionSemantics = "api"

    @property
    def scope(self) -> str:
        return self.scope_alias or "github." + self.operation

    @property
    def write(self) -> bool:
        return self.method != "GET"

    def descriptor(self) -> CapabilityDescriptor:
        local_mutation = self.execution_semantics in {
            "workspace_mutation",
            "command_launch",
            "command_cancel",
            "workspace_close",
        }
        side_effect = self.write or local_mutation
        return CapabilityDescriptor(
            scope=self.scope,
            provider="github",
            resource_type=self.resource_type,
            operation=self.operation,
            mode="action"
            if local_mutation or self.execution_semantics == "initialization"
            else "write"
            if self.write
            else "read",
            risk="high" if self.approval else "medium" if side_effect else "low",
            requires_credential=self.write or self.resource_type == "project",
            side_effect=side_effect,
            approval_recommendation="required" if self.approval else "none",
            input_schema={
                "type": "object",
                "properties": self.fields,
                "required": list(self.required),
                "additionalProperties": False,
            },
            output_schema={"type": ["object", "array"]},
            description=self.description,
            target=self.operation.split(".")[1],
        )


def action(
    operation: str,
    description: str,
    permission: str,
    path: str = "",
    *,
    method="GET",
    fields=None,
    required=(),
    query=(),
    approval=False,
    resource_type="repository",
    graphql="",
    scope_alias="",
    verification="canonical_read",
    reconciliation="none",
    prerequisites=(),
    execution_semantics: ExecutionSemantics = "api",
) -> GitHubAction:
    return GitHubAction(
        operation,
        description,
        permission,
        method,
        path,
        fields or {},
        tuple(required),
        tuple(query),
        approval,
        resource_type,
        graphql,
        scope_alias,
        verification,
        reconciliation,
        tuple(prerequisites),
        execution_semantics,
    )


def paged(**fields):
    return {"page": PAGE, "per_page": {"type": "integer", "minimum": 1, "maximum": 100}, **fields}


def assembled_catalog() -> tuple[GitHubAction, ...]:
    from .actions.automation import ACTIONS as automation
    from .actions.coding import ACTIONS as coding
    from .actions.community import ACTIONS as community
    from .actions.issues import ACTIONS as issues
    from .actions.pulls import ACTIONS as pulls
    from .actions.repository import ACTIONS as repository

    all_actions = repository + issues + pulls + automation + community + coding
    assert len({a.scope for a in all_actions}) == len(all_actions)
    assert len({a.operation for a in all_actions}) == len(all_actions)
    return all_actions
