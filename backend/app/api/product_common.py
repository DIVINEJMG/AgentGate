from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.models import AuditEvent


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def require_permission(principal: HumanPrincipal, permission: str) -> None:
    if permission not in principal.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required permission: {permission}",
        )


def not_found(label: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found.")


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


async def append_audit(
    session: AsyncSession,
    *,
    organization_id: UUID,
    principal: HumanPrincipal | None,
    event_type: str,
    category: str,
    severity: str = "info",
    resource_type: str,
    resource_id: str,
    resource_name: str | None = None,
    correlation_id: str | None = None,
    outcome: str | None = None,
    summary: str,
    metadata: dict[str, Any] | None = None,
    actor: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=organization_id,
        event_type=event_type,
        category=category,
        severity=severity,
        correlation_id=correlation_id,
        actor=actor
        or {
            "type": "human" if principal is not None else "system",
            "id": str(principal.user_id) if principal is not None else "system",
            "label": None,
        },
        resource={"type": resource_type, "id": resource_id, "name": resource_name},
        payload={
            "outcome": outcome,
            "summary": summary,
            "metadata": metadata or {},
        },
    )
    session.add(event)
    await session.flush()
    return event


async def latest_setting(
    session: AsyncSession,
    organization_id: UUID,
    event_type: str,
) -> dict[str, Any] | None:
    event = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == organization_id,
            AuditEvent.event_type == event_type,
        )
        .order_by(desc(AuditEvent.created_at))
        .limit(1)
    )
    if event is None:
        return None
    metadata = event.payload.get("metadata") if isinstance(event.payload, dict) else None
    return metadata if isinstance(metadata, dict) else None


def audit_public(event: AuditEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    actor = event.actor if isinstance(event.actor, dict) else {}
    resource = event.resource if isinstance(event.resource, dict) else {}
    return {
        "id": str(event.id),
        "organizationId": str(event.organization_id),
        "eventType": event.event_type,
        "category": event.category,
        "severity": event.severity,
        "actor": {
            "type": actor.get("type", "system"),
            "id": str(actor.get("id", "system")),
            "label": actor.get("label"),
        },
        "resource": {
            "type": resource.get("type", "unknown"),
            "id": str(resource.get("id", "")),
            "name": resource.get("name"),
        },
        "correlationId": event.correlation_id,
        "outcome": payload.get("outcome"),
        "summary": str(payload.get("summary") or event.event_type),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "occurredAt": event.created_at.isoformat(),
    }
