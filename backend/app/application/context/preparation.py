"""The context planner for task preparation: each resource once, with its tools stated as the
difference from its provider's full set rather than listed in full."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.application.context.evidence import as_list

RESOURCE_FIELDS = ("id", "name", "provider", "account", "health", "aliases")


def preparation_resources(catalog: Iterable[Mapping[str, object]],
                          capabilities: Mapping[str, Mapping[str, object]]) -> list[dict[str, Any]]:
    """Resources for preparation. `tools` is "all" when the resource has every tool its provider
    declares; otherwise whichever is shorter of its tools or its `missingTools`."""
    declared = {provider: set(tools) for provider, tools in capabilities.items()}
    shown: list[dict[str, Any]] = []
    for resource in catalog:
        record: dict[str, Any] = {key: resource[key] for key in RESOURCE_FIELDS if resource.get(key)}
        tools = {str(scope) for scope in as_list(resource.get("capabilities"))}
        missing = sorted(declared.get(str(resource.get("provider")), set()) - tools)
        if not missing:
            record["tools"] = "all"
        elif len(missing) < len(tools):
            record["missingTools"] = missing
        else:
            record["tools"] = sorted(tools)
        shown.append(record)
    return shown
