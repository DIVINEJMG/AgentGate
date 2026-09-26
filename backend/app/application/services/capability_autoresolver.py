from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.browser_origin_authority import (
    browser_integration_origins,
    browser_origins_covered,
)
from app.domain.ai.providers import AIGateway, AIInvocationContext
from app.domain.workforce.drafts import CapabilityNeed
from app.execution.bootstrap import execution_provider_registry
from app.infrastructure.database.models import Integration, IntegrationCredential


@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    scopes: tuple[str, ...]
    missing_integrations: tuple[str, ...]
    mappings: tuple[dict[str, object], ...]


def _provider_id(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "drive": "google_drive",
        "google_drive": "google_drive",
        "calendar": "google_calendar",
        "google_calendar": "google_calendar",
        "web": "browser",
        "chrome": "browser",
    }
    return aliases.get(normalized, normalized)


class SemanticCapabilityResolver:
    """Map semantic AI needs onto the exact live Aduoryn capability catalog.

    Model output is constrained to enums built from the currently connected provider
    catalog, so a model cannot grant or invent a capability scope.
    """

    def __init__(self, session: AsyncSession, gateway: AIGateway) -> None:
        self._session = session
        self._gateway = gateway
        self._registry = execution_provider_registry()

    async def resolve(
        self,
        *,
        organization_id: UUID,
        needs: list[CapabilityNeed],
        invocation_context: AIInvocationContext,
        required_browser_origins: tuple[str, ...] = (),
    ) -> CapabilityResolution:
        integrations = list(
            (
                await self._session.scalars(
                    select(Integration).where(
                        Integration.organization_id == organization_id,
                        Integration.status == "connected",
                    )
                )
            ).all()
        )
        by_provider: dict[str, list[Integration]] = {}
        for integration in integrations:
            by_provider.setdefault(integration.provider, []).append(integration)

        missing: set[str] = set()
        candidates: dict[str, dict[str, object]] = {}
        semantic: list[dict[str, object]] = []

        for need in needs:
            provider_id = _provider_id(need.provider)
            try:
                provider = self._registry.get(provider_id)
            except KeyError as exc:
                raise ValueError(
                    f"Worker draft requested unsupported provider {need.provider}."
                ) from exc

            connected = by_provider.get(provider_id, [])
            browser_origin_gap = False
            if provider_id == "browser" and required_browser_origins:
                browser_origin_gap = not browser_origins_covered(
                    connected,
                    required_browser_origins,
                )
                required_origin_set = set(required_browser_origins)
                connected = [
                    integration
                    for integration in connected
                    if (
                        (resource_origins := set(browser_integration_origins(integration)))
                        and resource_origins.issubset(required_origin_set)
                    )
                ]
            if not connected:
                missing.add(provider_id)
                semantic.append(
                    {
                        "provider": provider_id,
                        "need": need.need,
                        "actions": need.actions,
                        "state": "missing_integration",
                    }
                )
                continue

            available: set[str] = set()
            for integration in connected:
                raw_config = integration.config if isinstance(integration.config, dict) else {}
                configured = raw_config.get("availableCapabilities")
                declared = (
                    {str(item) for item in configured}
                    if isinstance(configured, list)
                    else {cap.scope for cap in provider.manifest.capabilities}
                )
                credential_id = await self._session.scalar(
                    select(IntegrationCredential.id).where(
                        IntegrationCredential.integration_id == integration.id
                    )
                )
                for capability in provider.manifest.capabilities:
                    if capability.scope not in declared:
                        continue
                    if capability.requires_credential and credential_id is None:
                        continue
                    available.add(capability.scope)
                    candidates[capability.scope] = {
                        "scope": capability.scope,
                        "provider": provider_id,
                        "operation": capability.operation,
                        "mode": capability.mode,
                        "risk": capability.risk,
                        "sideEffect": capability.side_effect,
                        "description": capability.description,
                        "target": capability.target,
                    }
            if not available or browser_origin_gap:
                missing.add(provider_id)
            semantic.append(
                {
                    "provider": provider_id,
                    "need": need.need,
                    "actions": need.actions,
                    "candidateScopes": sorted(available),
                    "requiredOrigins": (
                        list(required_browser_origins)
                        if provider_id == "browser"
                        else []
                    ),
                    "matchedOrigins": (
                        sorted(
                            {
                                origin
                                for integration in connected
                                for origin in browser_integration_origins(integration)
                                if origin in set(required_browser_origins)
                            }
                        )
                        if provider_id == "browser"
                        else []
                    ),
                    "state": (
                        "missing_authorized_origin"
                        if browser_origin_gap
                        else "connected"
                        if available
                        else "missing_usable_capability"
                    ),
                }
            )

        if not candidates:
            return CapabilityResolution(
                scopes=(),
                missing_integrations=tuple(sorted(missing)),
                mappings=tuple(semantic),
            )

        allowed_scopes = sorted(candidates)
        schema: dict[str, object] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selectedScopes": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": allowed_scopes},
                }
            },
            "required": ["selectedScopes"],
        }
        parsed = await self._gateway.generate_structured(
            role="intent",
            system=(
                "Map semantic Worker capability needs to the smallest exact set of Aduoryn "
                "capability scopes needed for the job. You may select ONLY values from the "
                "provided candidateScopes enum. Capabilities are permissions/tools, not steps. "
                "Prefer read/observe scopes when the objective only requires checking status. "
                "Select write/action scopes only when explicitly required by the job."
            ),
            prompt=(
                "SEMANTIC_NEEDS:\n"
                + json.dumps(semantic, separators=(",", ":"), default=str)
                + "\n\nLIVE_CAPABILITY_CATALOG:\n"
                + json.dumps(list(candidates.values()), separators=(",", ":"), default=str)
            ),
            schema_name="capability_resolution_v1",
            schema=schema,
            context=invocation_context,
            max_output_tokens=1000,
        )
        raw_selected = parsed.get("selectedScopes")
        selected = [str(item) for item in raw_selected] if isinstance(raw_selected, list) else []
        invalid = set(selected) - set(allowed_scopes)
        if invalid:
            raise ValueError("Capability resolver returned a scope outside the live catalog.")
        return CapabilityResolution(
            scopes=tuple(sorted(set(selected))),
            missing_integrations=tuple(sorted(missing)),
            mappings=tuple(semantic),
        )
