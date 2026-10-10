"""Read-only web research capability for managed Jobs."""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.services.web_research import configured_sources, research_web
from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    ProviderHealth,
    ProviderManifest,
    ResourceDescriptor,
)
from app.execution.providers.native.base import NativeProvider
from app.infrastructure.ai.provider import ai_gateway_from_settings

CAPABILITIES = (
    CapabilityDescriptor(
        scope="web.research",
        provider="web_research",
        resource_type="public_web",
        operation="web.research",
        mode="read",
        risk="low",
        requires_credential=False,
        side_effect=False,
        approval_recommendation="none",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "requiredSource": {"type": "string", "enum": ["brave", "tavily"]},
                "requiredSite": {"type": "string", "maxLength": 200},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        description="Search public sources, inspect bounded read-only pages, and return a cited answer.",
        target="public_web",
    ),
)


class WebResearchProvider(NativeProvider):
    manifest = ProviderManifest(
        provider="web_research",
        display_name="Public web research",
        kind="native_api",
        version="1.0.0",
        credential_strategy="none",
        capabilities=CAPABILITIES,
    )

    async def discover_resources(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> tuple[ResourceDescriptor, ...]:
        del configuration, credential
        return (
            ResourceDescriptor(
                id="web_research:public",
                provider="web_research",
                resource_type="public_web",
                external_id="public",
                display_name="Public web research",
                metadata={"readOnly": True},
                health="healthy" if configured_sources() else "unavailable",
                available_capabilities=("web.research",),
                configuration={"readOnly": "true"},
            ),
        )

    async def check_health(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ProviderHealth:
        del configuration, credential
        ready = bool(configured_sources())
        return ProviderHealth(
            state="healthy" if ready else "unavailable",
            message="Search provider configured." if ready else "No search provider configured.",
            checked_at=datetime.now(UTC),
        )

    async def normalize_input(
        self,
        *,
        operation: str,
        input: dict[str, object],
    ) -> dict[str, object]:
        query = str(input.get("query") or "").strip()
        if operation != "web.research" or not query or len(query) > 500:
            raise ValueError("A bounded web research query is required.")
        source = str(input.get("requiredSource") or "").strip().lower()
        if source and source not in {"brave", "tavily"}:
            raise ValueError("Unsupported required search source.")
        site = str(input.get("requiredSite") or "").strip()[:200]
        return {
            "query": query,
            **({"requiredSource": source} if source else {}),
            **({"requiredSite": site} if site else {}),
        }

    async def execute(
        self,
        *,
        request: ExecutionRequest,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ExecutionResult:
        del configuration, credential
        started = datetime.now(UTC)
        query = str(request.input.get("query") or "").strip()
        if not query:
            raise ValueError("A web research query is required.")
        result = await research_web(
            query=query,
            required_source=str(request.input.get("requiredSource") or "") or None,
            organization_id=request.organization_id,
            worker_id=request.worker_id,
            thread_id=None,
            gateway=ai_gateway_from_settings(workload="complex", purpose="web_research"),
            required_site=str(request.input.get("requiredSite") or "") or None,
        )
        return self._result(
            request=request,
            output={
                "message": result.message,
                "sources": [source.url for source in result.sources],
                "attempts": list(result.attempts),
            },
            provider_request_id=None,
            started_at=started,
        )


web_research_provider = WebResearchProvider()
