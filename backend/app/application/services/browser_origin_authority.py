from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.jobs_routes import update_job
from app.api.product_common import append_audit, utcnow
from app.domain.identity.principals import HumanPrincipal
from app.execution.bootstrap import execution_provider_registry
from app.execution.browser.policy import normalize_origin
from app.infrastructure.database.models import (
    ConversationMessage,
    Integration,
    Job,
    JobRevision,
)

_URL_PATTERN = re.compile(r"https?://[^\s<>\"'\x60]+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?)]}"


def explicit_http_origins(text: str) -> tuple[str, ...]:
    """Return exact HTTP(S) origins explicitly present in human-authored text."""

    origins: list[str] = []
    for match in _URL_PATTERN.findall(text):
        candidate = match.rstrip(_TRAILING_URL_PUNCTUATION)
        origin = normalize_origin(candidate)
        if origin is not None and origin not in origins:
            origins.append(origin)
    return tuple(origins)


def browser_integration_origins(integration: Integration | None) -> tuple[str, ...]:
    if integration is None or integration.provider != "browser":
        return ()

    config = integration.config if isinstance(integration.config, dict) else {}
    metadata = config.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    values: list[str] = []
    raw_metadata = metadata.get("allowedOrigins")
    if isinstance(raw_metadata, list):
        values.extend(str(item) for item in raw_metadata if str(item).strip())

    raw_allowed = config.get("allowedOrigins")
    if isinstance(raw_allowed, str):
        values.extend(item.strip() for item in raw_allowed.split(",") if item.strip())

    for key in ("startUrl", "webUrl", "resourceKey"):
        raw = config.get(key)
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())

    origins: list[str] = []
    for value in values:
        origin = normalize_origin(value)
        if origin is not None and origin not in origins:
            origins.append(origin)
    return tuple(origins)


def browser_origins_covered(
    integrations: Iterable[Integration],
    required_origins: Iterable[str],
) -> bool:
    required = {
        origin
        for value in required_origins
        if (origin := normalize_origin(str(value))) is not None
    }
    if not required:
        return True

    available: set[str] = set()
    for integration in integrations:
        resource_origins = set(browser_integration_origins(integration))
        if resource_origins and resource_origins.issubset(required):
            available.update(resource_origins)
    return required.issubset(available)


async def ensure_managed_browser_origins(
    session: AsyncSession,
    *,
    organization_id: UUID,
    origins: Iterable[str],
    principal: HumanPrincipal | None,
    source: str,
) -> tuple[Integration, ...]:
    """Create one least-authority Browser resource per explicit human URL origin.

    This never broadens an existing Browser resource. Existing resources that
    already authorize an origin are reused; otherwise a new credential-free,
    public-network-only resource is created for that exact origin.
    """

    requested: list[str] = []
    for value in origins:
        origin = normalize_origin(str(value))
        if origin is not None and origin not in requested:
            requested.append(origin)
    if not requested:
        return ()

    existing = list(
        (
            await session.scalars(
                select(Integration).where(
                    Integration.organization_id == organization_id,
                    Integration.provider == "browser",
                    Integration.status.in_(["connected", "degraded"]),
                )
            )
        ).all()
    )

    resolved: list[Integration] = []
    covered: dict[str, Integration] = {}
    for integration in existing:
        for origin in browser_integration_origins(integration):
            covered.setdefault(origin, integration)

    provider = execution_provider_registry().get("browser")
    manifest = provider.manifest
    actor_id = str(principal.user_id) if principal is not None else "system"

    for origin in requested:
        current = covered.get(origin)
        if current is not None:
            resolved.append(current)
            continue

        host = urlsplit(origin).hostname or origin
        configuration = {
            "startUrl": f"{origin}/",
            "allowedOrigins": origin,
            "displayName": f"Managed web · {host}",
            "enableFileTransfer": "false",
            "allowPrivateNetwork": "false",
        }
        resources = await provider.discover_resources(
            configuration=configuration,
            credential=None,
        )
        if len(resources) != 1:
            raise RuntimeError(
                "Managed Browser origin discovery did not resolve exactly one resource."
            )
        resource = resources[0]
        now = utcnow()
        integration = Integration(
            organization_id=organization_id,
            provider="browser",
            display_name=resource.display_name,
            status="connected",
            config={
                **resource.configuration,
                "resourceKey": resource.external_id,
                "resourceType": resource.resource_type,
                "webUrl": resource.web_url or "",
                "metadata": resource.metadata,
                "supportedOperations": [
                    capability.operation for capability in manifest.capabilities
                ],
                "availableCapabilities": list(resource.available_capabilities),
                "providerKind": manifest.kind,
                "adapterVersion": manifest.version,
                "credentialFingerprint": None,
                "healthMessage": "Governed Browser origin provisioned from explicit human instruction.",
                "lastCheckedAt": now.isoformat(),
                "createdBy": actor_id,
                "managedBy": "worker_autonomy",
                "managedOrigin": origin,
                "managedSource": source,
            },
            created_at=now,
            updated_at=now,
        )
        session.add(integration)
        await session.flush()
        await append_audit(
            session,
            organization_id=organization_id,
            principal=principal,
            event_type="integration.browser_origin.auto_connected",
            category="integration",
            resource_type="integration",
            resource_id=str(integration.id),
            resource_name=integration.display_name,
            outcome="connected",
            summary=f"Managed Browser origin {origin} provisioned.",
            metadata={
                "provider": "browser",
                "origin": origin,
                "managedBy": "worker_autonomy",
                "source": source,
                "credentialConfigured": False,
            },
        )
        covered[origin] = integration
        resolved.append(integration)

    await session.commit()
    return tuple(resolved)


async def reconcile_ai_job_browser_origins(
    session: AsyncSession,
    *,
    organization_id: UUID,
    job_id: UUID,
    principal: HumanPrincipal,
) -> tuple[Job, tuple[str, ...], int]:
    """Backfill managed Browser origins for an existing AI-created Job.

    Authorization comes only from the original human conversation message referenced
    by the Job's immutable autonomy provenance.
    """

    job = await session.scalar(
        select(Job).where(
            Job.organization_id == organization_id,
            Job.id == job_id,
        )
    )
    if job is None:
        raise LookupError("Job not found.")

    revision = await session.scalar(
        select(JobRevision)
        .where(JobRevision.job_id == job.id)
        .order_by(JobRevision.revision.desc())
        .limit(1)
    )
    if revision is None:
        raise LookupError("Job revision not found.")
    definition = dict(revision.definition or {}) if isinstance(revision.definition, dict) else {}
    raw_autonomy = definition.get("autonomy")
    autonomy = dict(raw_autonomy) if isinstance(raw_autonomy, dict) else {}
    if not bool(autonomy.get("createdByAI")):
        raise PermissionError("Only AI-created Jobs can use Browser-origin reconciliation.")

    raw_source = autonomy.get("sourceMessageId")
    try:
        source_message_id = UUID(str(raw_source))
    except (TypeError, ValueError) as exc:
        raise ValueError("AI-created Job is missing human source-message provenance.") from exc

    source_message = await session.scalar(
        select(ConversationMessage).where(
            ConversationMessage.organization_id == organization_id,
            ConversationMessage.id == source_message_id,
            ConversationMessage.role == "human",
        )
    )
    if source_message is None:
        raise LookupError("Human source message is unavailable.")

    origins = explicit_http_origins(source_message.content)
    if not origins:
        raise ValueError("The source message contains no explicit HTTP(S) origin to authorize.")

    before = list(
        (
            await session.scalars(
                select(Integration).where(
                    Integration.organization_id == organization_id,
                    Integration.provider == "browser",
                    Integration.status.in_(["connected", "degraded"]),
                )
            )
        ).all()
    )
    before_ids = {item.id for item in before}
    resolved = await ensure_managed_browser_origins(
        session,
        organization_id=organization_id,
        origins=origins,
        principal=principal,
        source=f"conversation:{source_message.id}",
    )
    created = sum(item.id not in before_ids for item in resolved)

    existing_origins = autonomy.get("authorizedBrowserOrigins")
    normalized_existing = (
        tuple(str(item) for item in existing_origins if str(item))
        if isinstance(existing_origins, list)
        else ()
    )
    if normalized_existing != origins:
        autonomy["authorizedBrowserOrigins"] = list(origins)
        job = await update_job(
            session,
            organization_id,
            job.id,
            principal,
            {"autonomy": autonomy},
        )
    return job, origins, created
