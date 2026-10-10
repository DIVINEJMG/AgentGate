"""Product security events and administrator notification sources (phase 7).

Security events carry only the fields Console's audit page shows. The audit ``actor``,
``resource`` and ``payload`` JSON is never selected: it is free-form and can hold
identifiers or nested keys Console must not receive. Notifications follow Console's
``ProductNotification`` model, including its stable fingerprints for de-duplication.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.engine import Connection

from app.application.console_reads.database import clip, iso
from app.application.console_reads.visibility import (
    _open_incident,
    audit_events,
    incidents,
    queue_failures,
)

NOTIFICATION_SOURCE_CAP = 20
SEVERE = ("high", "critical")


def read_security_events(connection: Connection, *, limit: int) -> list[dict[str, object]]:
    category = func.lower(audit_events.c.category)
    rows = connection.execute(
        select(
            audit_events.c.id,
            audit_events.c.organization_id,
            audit_events.c.event_type,
            audit_events.c.category,
            audit_events.c.severity,
            audit_events.c.correlation_id,
            audit_events.c.created_at,
        )
        .where(
            or_(
                func.lower(audit_events.c.severity).in_(SEVERE),
                category.like("%security%"),
                category.like("%auth%"),
            )
        )
        .order_by(audit_events.c.created_at.desc(), audit_events.c.id)
        .limit(limit)
    )
    return [
        {
            "id": str(row.id),
            "organizationId": str(row.organization_id),
            "eventType": row.event_type,
            "category": row.category,
            "severity": row.severity,
            "correlationId": row.correlation_id,
            "createdAt": iso(row.created_at),
        }
        for row in rows
    ]


def read_notifications(connection: Connection, *, limit: int) -> list[dict[str, object]]:
    """Open high/critical incidents and unresolved delivery failures, newest first."""
    per_source = min(NOTIFICATION_SOURCE_CAP, limit)
    incident_rows = connection.execute(
        select(incidents.c.id, incidents.c.summary)
        .where(_open_incident(incidents), func.lower(incidents.c.severity).in_(SEVERE))
        .order_by(incidents.c.created_at.desc(), incidents.c.id)
        .limit(per_source)
    )
    failure_rows = connection.execute(
        select(queue_failures.c.id, queue_failures.c.source_message_id)
        .where(queue_failures.c.resolved_at.is_(None))
        .order_by(queue_failures.c.created_at.desc(), queue_failures.c.id)
        .limit(per_source)
    )
    notifications: list[dict[str, object]] = [
        {
            "source": "audoryn.incident",
            "severity": "critical",
            "title": "Audoryn product incident",
            "body": clip(row.summary, 4000),
            "href": "/operations/system-health",
            "fingerprint": f"audoryn-incident:{row.id}",
        }
        for row in incident_rows
    ]
    failures: list[dict[str, object]] = [
        {
            "source": "audoryn.queue",
            "severity": "warning",
            "title": "Audoryn queue delivery failure",
            "body": clip(f"Message {row.source_message_id} is unresolved.", 4000),
            "href": "/operations/queues",
            "fingerprint": f"audoryn-queue:{row.id}",
        }
        for row in failure_rows
    ]
    return (notifications + failures)[:limit]
