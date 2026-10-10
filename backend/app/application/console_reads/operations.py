"""Product operations reads: health counts, recent incidents, work-item and delivery-failure queues.

Shapes are camelCase because Console's observability adapter checks these exact keys
(``Audoryn-Console/backend/app/infrastructure/agentgate/observability.py``). Delivery
failures never include ``failure_payload``; destination URLs lose credentials, query and fragment.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import case, func, select
from sqlalchemy.engine import Connection

from app.application.console_reads.database import clip, iso, iso_or_none
from app.application.console_reads.visibility import (
    SUMMARY_CAP,
    _failed,
    _ints,
    _n,
    _open_incident,
    _t,
    _values,
    incidents,
    queue_failures,
    runs,
)
from app.infrastructure.database import models as m

work_items = _t(m.WorkItem)
outbox_events = _t(m.OutboxEvent)

RECENT_INCIDENT_CAP = 25
WORK_ITEM_STATUS_CAP = 50
QUEUED_STATES = ("queued", "waiting_ai", "waiting_configuration")
SEVERE = ("high", "critical")


def product_state(*, critical_incidents: int, open_incidents: int, unresolved_queue_failures: int, failed_runs_24h: int) -> str:
    if critical_incidents > 0:
        return "critical"
    if open_incidents > 0 or unresolved_queue_failures > 0 or failed_runs_24h > 0:
        return "degraded"
    return "healthy"


def read_operations(connection: Connection, *, now: datetime) -> dict[str, object]:
    day = now - timedelta(hours=24)
    counts = _ints(
        _values(
            connection,
            open_incidents=_n(incidents, _open_incident(incidents)),
            critical=_n(incidents, _open_incident(incidents), func.lower(incidents.c.severity).in_(SEVERE)),
            unresolved=_n(queue_failures, queue_failures.c.resolved_at.is_(None)),
            failed_24h=_n(runs, runs.c.created_at >= day, _failed(runs.c.status)),
            queued=_n(work_items, func.lower(work_items.c.status).in_(QUEUED_STATES)),
            unpublished=_n(outbox_events, outbox_events.c.published_at.is_(None)),
        )
    )
    open_incidents, critical = counts["open_incidents"], counts["critical"]
    unresolved, failed_24h = counts["unresolved"], counts["failed_24h"]
    severity_rank = case(
        (func.lower(incidents.c.severity) == "critical", 0),
        (func.lower(incidents.c.severity) == "high", 1),
        (func.lower(incidents.c.severity) == "medium", 2),
        else_=3,
    )
    recent = connection.execute(
        select(
            incidents.c.id,
            incidents.c.organization_id,
            incidents.c.status,
            incidents.c.severity,
            incidents.c.summary,
            incidents.c.created_at,
            incidents.c.updated_at,
        )
        .where(_open_incident(incidents))
        .order_by(severity_rank, incidents.c.created_at.desc(), incidents.c.id)
        .limit(RECENT_INCIDENT_CAP)
    )
    return {
        "state": product_state(
            critical_incidents=critical,
            open_incidents=open_incidents,
            unresolved_queue_failures=unresolved,
            failed_runs_24h=failed_24h,
        ),
        "openIncidents": open_incidents,
        "criticalIncidents": critical,
        "unresolvedQueueFailures": unresolved,
        "failedRuns24h": failed_24h,
        "queuedWorkItems": counts["queued"],
        "unpublishedOutbox": counts["unpublished"],
        "recentIncidents": [
            {
                "id": str(item.id),
                "organizationId": str(item.organization_id),
                "status": item.status,
                "severity": item.severity,
                "summary": clip(item.summary, SUMMARY_CAP),
                "createdAt": iso(item.created_at),
                "updatedAt": iso(item.updated_at),
            }
            for item in recent
        ],
    }


def safe_destination(url: str | None) -> str:
    """Scheme, host, port and path only: user info, query and fragment can carry secrets."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if not parts.scheme or not host:
        return ""
    netloc = f"{host}:{port}" if port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))[:2048]


def read_queues(connection: Connection, *, limit: int) -> dict[str, object]:
    status = func.lower(work_items.c.status)
    counts = connection.execute(
        select(status.label("status"), func.count().label("count"))
        .group_by(status)
        .order_by(func.count().desc(), status)
        .limit(WORK_ITEM_STATUS_CAP)
    )
    # failure_payload is never selected.
    failures = connection.execute(
        select(
            queue_failures.c.id,
            queue_failures.c.organization_id,
            queue_failures.c.source_message_id,
            queue_failures.c.dlq_id,
            queue_failures.c.status_code,
            queue_failures.c.retried,
            queue_failures.c.max_retries,
            queue_failures.c.destination_url,
            queue_failures.c.resolved_at,
            queue_failures.c.created_at,
        )
        .order_by(queue_failures.c.created_at.desc(), queue_failures.c.id)
        .limit(limit)
    )

    def failure(item: Any) -> dict[str, object]:
        return {
            "id": str(item.id),
            "organizationId": str(item.organization_id),
            "sourceMessageId": item.source_message_id,
            "dlqId": item.dlq_id,
            "statusCode": item.status_code,
            "retried": int(item.retried or 0),
            "maxRetries": int(item.max_retries or 0),
            "destinationUrl": safe_destination(item.destination_url),
            "resolvedAt": iso_or_none(item.resolved_at),
            "createdAt": iso(item.created_at),
        }

    return {
        "audorynWorkItems": {str(state): int(total) for state, total in counts},
        "deliveryFailures": [failure(item) for item in failures],
    }
