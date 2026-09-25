from __future__ import annotations

from collections import Counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import SecretStr
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import require_permission
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.models import AIInvocation, WorkItem
from app.infrastructure.database.session import database_session

v1_router = APIRouter(tags=["ai-operations"])
v2_router = APIRouter(tags=["ai-operations"])

_MAX_INVOCATIONS = 100
_MAX_WAITING = 500


def _secret_configured(secret: SecretStr | None) -> bool:
    return bool(secret is not None and secret.get_secret_value().strip())


def _credential_status() -> tuple[bool, bool]:
    legacy = _secret_configured(settings.ai_provider_api_key)
    coordinator = _secret_configured(settings.ai_coordinator_api_key) or legacy
    vision = _secret_configured(settings.ai_vision_api_key) or legacy
    return coordinator, vision


async def _snapshot(
    organization_id: UUID,
    principal: HumanPrincipal,
    session: AsyncSession,
) -> dict[str, object]:
    require_permission(principal, "audit.read")

    invocations = list(
        (
            await session.scalars(
                select(AIInvocation)
                .where(AIInvocation.organization_id == organization_id)
                .order_by(desc(AIInvocation.created_at))
                .limit(_MAX_INVOCATIONS)
            )
        ).all()
    )
    waiting = list(
        (
            await session.scalars(
                select(WorkItem)
                .where(
                    WorkItem.organization_id == organization_id,
                    WorkItem.status.in_(["waiting_ai", "waiting_configuration"]),
                )
                .order_by(WorkItem.scheduled_at)
                .limit(_MAX_WAITING)
            )
        ).all()
    )

    total = len(invocations)
    successes = sum(1 for item in invocations if item.success)
    failures = total - successes
    latencies = [item.latency_ms for item in invocations if item.latency_ms is not None]
    average_latency_ms = round(sum(latencies) / len(latencies), 1) if latencies else None

    errors = Counter(
        item.error_category
        for item in invocations
        if item.error_category
    )
    role_counts = Counter(item.role for item in invocations)
    role_routing: dict[str, dict[str, object]] = {}
    for role in sorted(role_counts):
        latest = next(item for item in invocations if item.role == role)
        role_routing[role] = {
            "provider": latest.provider,
            "model": latest.model,
            "invocations": role_counts[role],
        }

    coordinator_configured, vision_configured = _credential_status()
    configured = settings.ai_enabled and coordinator_configured and vision_configured
    if not settings.ai_enabled:
        provider_health = "disabled"
    elif not configured:
        provider_health = "unconfigured"
    elif invocations and not invocations[0].success:
        provider_health = "degraded"
    else:
        provider_health = "ready"

    return {
        "configuration": {
            "enabled": settings.ai_enabled,
            "configured": configured,
            "coordinatorConfigured": coordinator_configured,
            "visionConfigured": vision_configured,
            "provider": settings.ai_provider,
            "providerHealth": provider_health,
            "coordinatorModel": settings.ai_coordinator_model,
            "visionModel": settings.ai_vision_model,
        },
        "invocations": {
            "sampleSize": total,
            "successes": successes,
            "failures": failures,
            "successRate": round((successes / total) * 100, 1) if total else None,
            "averageLatencyMs": average_latency_ms,
            "errorsByCategory": dict(sorted(errors.items())),
            "roleRouting": role_routing,
            "window": {
                "limit": _MAX_INVOCATIONS,
                "truncated": total >= _MAX_INVOCATIONS,
            },
        },
        "planner": {
            "waitingCount": len(waiting),
            "waitingConfiguration": sum(
                1 for item in waiting if item.status == "waiting_configuration"
            ),
            "waitingProvider": sum(1 for item in waiting if item.status == "waiting_ai"),
            "window": {
                "limit": _MAX_WAITING,
                "truncated": len(waiting) >= _MAX_WAITING,
            },
        },
    }


@v1_router.get("/organizations/{organization_id}/ai/operations")
async def ai_operations_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    return await _snapshot(organization_id, principal, session)


@v2_router.get("/organizations/{organization_id}/ai/operations")
async def ai_operations_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    return {"data": await _snapshot(organization_id, principal, session)}
