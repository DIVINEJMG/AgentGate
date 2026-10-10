"""Platform search and support views (phase 8).

camelCase shapes match Console's checks in ``observability.py``: search items carry
exactly ``type, id, title, subtitle, href``; support views return only the AgentGate
half, and Console adds its own billing and administrative history. No impersonation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ColumnElement

from app.application.console_reads.database import clip, iso, like_pattern
from app.application.console_reads.visibility import (
    MEMBERSHIP_CAP,
    SUMMARY_CAP,
    _failed,
    _ints,
    _n,
    _open_incident,
    _values,
    identities,
    incidents,
    memberships,
    organizations,
    queue_failures,
    runs,
    workers,
)

SUPPORT_INCIDENT_CAP = 25


def _uuid_or_none(value: str) -> UUID | None:
    try:
        return UUID(value) if len(value) == 36 else None
    except ValueError:
        return None


def read_search(connection: Connection, *, query: str, limit_per_type: int) -> dict[str, object]:
    pattern = like_pattern(query)
    exact = _uuid_or_none(query)
    user_match: list[ColumnElement[bool]] = [
        func.coalesce(identities.c.display_name, "").ilike(pattern, escape="\\"),
        func.coalesce(identities.c.email, "").ilike(pattern, escape="\\"),
        identities.c.subject.ilike(pattern, escape="\\"),
    ]
    organization_match: list[ColumnElement[bool]] = [organizations.c.name.ilike(pattern, escape="\\")]
    if exact is not None:  # a pasted full ID finds its record
        user_match.append(identities.c.id == exact)
        organization_match.append(organizations.c.id == exact)
    users = connection.execute(
        select(identities.c.id, identities.c.display_name, identities.c.email, identities.c.subject)
        .where(or_(*user_match))
        .order_by(identities.c.created_at.desc(), identities.c.id)
        .limit(limit_per_type)
    )
    found_organizations = connection.execute(
        select(organizations.c.id, organizations.c.name)
        .where(or_(*organization_match))
        .order_by(organizations.c.created_at.desc(), organizations.c.id)
        .limit(limit_per_type)
    )
    items: list[dict[str, object]] = [
        {
            "type": "user",
            "id": str(row.id),
            "title": row.display_name or row.email or row.subject,
            "subtitle": row.email or row.subject,
            "href": f"/users/{row.id}",
        }
        for row in users
    ]
    items += [
        {
            "type": "organization",
            "id": str(row.id),
            "title": row.name,
            "subtitle": str(row.id),
            "href": f"/organizations/{row.id}",
        }
        for row in found_organizations
    ]
    return {"items": items}


def read_support_user(connection: Connection, *, user_id: UUID) -> dict[str, object] | None:
    identity = connection.execute(
        select(
            identities.c.id,
            identities.c.subject,
            identities.c.email,
            identities.c.display_name,
            identities.c.created_at,
            identities.c.updated_at,
        ).where(identities.c.id == user_id)
    ).first()
    if identity is None:
        return None
    rows = connection.execute(
        select(memberships.c.organization_id, organizations.c.name, memberships.c.role, memberships.c.created_at)
        .join(organizations, organizations.c.id == memberships.c.organization_id)
        .where(memberships.c.user_id == user_id)
        .order_by(organizations.c.name, memberships.c.created_at)
        .limit(MEMBERSHIP_CAP)
    )
    return {
        "identity": {
            "id": str(identity.id),
            "subject": identity.subject,
            "email": identity.email,
            "displayName": identity.display_name,
            "createdAt": iso(identity.created_at),
            "updatedAt": iso(identity.updated_at),
        },
        "memberships": [
            {
                "organizationId": str(row.organization_id),
                "organizationName": row.name,
                "role": row.role,
                "joinedAt": iso(row.created_at),
            }
            for row in rows
        ],
    }


def read_support_organization(
    connection: Connection, *, organization_id: UUID, now: datetime
) -> dict[str, object] | None:
    organization = connection.execute(
        select(organizations.c.id, organizations.c.name, organizations.c.created_at, organizations.c.updated_at).where(
            organizations.c.id == organization_id
        )
    ).first()
    if organization is None:
        return None
    month = now - timedelta(days=30)
    scoped = organization_id
    recent = connection.execute(
        select(incidents.c.id, incidents.c.status, incidents.c.severity, incidents.c.summary, incidents.c.created_at)
        .where(incidents.c.organization_id == scoped)
        .order_by(incidents.c.created_at.desc(), incidents.c.id)
        .limit(SUPPORT_INCIDENT_CAP)
    )
    return {
        "organization": {
            "id": str(organization.id),
            "name": organization.name,
            "createdAt": iso(organization.created_at),
            "updatedAt": iso(organization.updated_at),
        },
        "state": _ints(
            _values(
                connection,
                members=_n(memberships, memberships.c.organization_id == scoped),
                workers=_n(workers, workers.c.organization_id == scoped),
                runs30d=_n(runs, runs.c.organization_id == scoped, runs.c.created_at >= month),
                failedRuns30d=_n(runs, runs.c.organization_id == scoped, runs.c.created_at >= month, _failed(runs.c.status)),
                openIncidents=_n(incidents, incidents.c.organization_id == scoped, _open_incident(incidents)),
                queueFailures=_n(
                    queue_failures, queue_failures.c.organization_id == scoped, queue_failures.c.resolved_at.is_(None)
                ),
            )
        ),
        "incidents": [
            {
                "id": str(row.id),
                "status": row.status,
                "severity": row.severity,
                "summary": clip(row.summary, SUMMARY_CAP),
                "createdAt": iso(row.created_at),
            }
            for row in recent
        ],
    }
