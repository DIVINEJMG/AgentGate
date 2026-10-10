"""Platform visibility reads: snapshot, overview, users, organisations.

Field names and meaning follow contract v1 (``docs/integration/console-reads``) and
Console's former shared-database adapter. Only allowlisted columns are selected: never
password hashes, integration configuration, payloads, prompts or message content.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Table, column, distinct, exists, func, select, table, union_all
from sqlalchemy.engine import Connection

from app.application.console_reads.database import clip, iso, iso_or_none, like_pattern
from app.infrastructure.database import models as m

MEMBER_CAP = 200
WORKER_CAP = 100
JOB_CAP = 100
INTEGRATION_CAP = 100
INCIDENT_CAP = 50
MEMBERSHIP_CAP = 200
ROLE_CAP = 20
SUMMARY_CAP = 2000
OPEN_INCIDENT_EXCLUDED = ("resolved", "closed")


def _t(model: Any) -> Table:
    return model.__table__


identities = _t(m.HumanIdentity)
organizations = _t(m.Organization)
memberships = _t(m.OrganizationMembership)
workers = _t(m.Worker)
jobs = _t(m.Job)
runs = _t(m.Run)
integrations = _t(m.Integration)
incidents = _t(m.Incident)
queue_failures = _t(m.QueueDeliveryFailure)
ai_invocations = _t(m.AIInvocation)
audit_events = _t(m.AuditEvent)
threads = _t(m.ConversationThread)
exports = _t(m.ResultExport)
approvals = _t(m.Approval)
local_credentials = _t(m.LocalAuthCredential)


def _count(connection: Connection, table: Table, *conditions: Any) -> int:
    return int(connection.scalar(select(func.count()).select_from(table).where(*conditions)) or 0)


def _n(table: Table, *conditions: Any) -> Any:
    """A count as a scalar subquery, so many counts share one round trip."""
    return select(func.count()).select_from(table).where(*conditions).scalar_subquery()


def _values(connection: Connection, **expressions: Any) -> dict[str, Any]:
    """Evaluate named scalar expressions in a single SELECT (one database round trip)."""
    row = connection.execute(select(*(expression.label(name) for name, expression in expressions.items()))).one()
    return dict(row._mapping)


def _ints(values: dict[str, Any]) -> dict[str, int]:
    return {name: int(value or 0) for name, value in values.items()}


def _failed(status_column: Any) -> Any:
    return func.lower(status_column) == "failed"


def _open_incident(table: Table) -> Any:
    return func.lower(table.c.status).not_in(OPEN_INCIDENT_EXCLUDED)


_alembic_version = table("alembic_version", column("version_num"))


def _migration_heads(connection: Connection) -> list[str]:
    heads = connection.scalars(select(_alembic_version.c.version_num)).all()
    return sorted(str(value) for value in heads)[:20]


# ---------- snapshot and overview ----------

def read_snapshot(connection: Connection) -> dict[str, int]:
    return _ints(_values(connection, human_identities=_n(identities), organizations=_n(organizations)))


def read_overview(connection: Connection, *, now: datetime) -> dict[str, object]:
    month = now - timedelta(days=30)
    attributed = union_all(
        select(organizations.c.created_by.label("user_id")).where(organizations.c.created_at >= month),
        select(threads.c.created_by.label("user_id")).where(threads.c.created_at >= month),
        select(exports.c.exported_by.label("user_id")).where(exports.c.created_at >= month),
        select(approvals.c.decided_by.label("user_id")).where(
            approvals.c.decided_by.is_not(None), approvals.c.updated_at >= month
        ),
    ).subquery()
    counts = _ints(
        _values(
            connection,
            total_users=_n(identities),
            active_users_30d=select(func.count(distinct(attributed.c.user_id))).scalar_subquery(),
            users_with_memberships=select(func.count(distinct(memberships.c.user_id))).scalar_subquery(),
            organizations=_n(organizations),
            active_organizations_30d=select(func.count(distinct(runs.c.organization_id)))
            .where(runs.c.created_at >= month)
            .scalar_subquery(),
            workers=_n(workers),
            runs_30d=_n(runs, runs.c.created_at >= month),
            failed_runs_30d=_n(runs, runs.c.created_at >= month, _failed(runs.c.status)),
            new_registrations_30d=_n(identities, identities.c.created_at >= month),
            integrations=_n(integrations),
            open_incidents=_n(incidents, _open_incident(incidents)),
            unresolved_queue_failures=_n(queue_failures, queue_failures.c.resolved_at.is_(None)),
        )
    )
    return {**counts, "agentgate_migration_heads": _migration_heads(connection)}


# ---------- users ----------

def _user_items(connection: Connection, rows: list[Any]) -> list[dict[str, object]]:
    ids = [row.id for row in rows]
    organizations_by_user: dict[UUID, set[UUID]] = defaultdict(set)
    roles_by_user: dict[UUID, set[str]] = defaultdict(set)
    if ids:
        for membership in connection.execute(
            select(memberships.c.user_id, memberships.c.organization_id, memberships.c.role).where(
                memberships.c.user_id.in_(ids)
            )
        ):
            organizations_by_user[membership.user_id].add(membership.organization_id)
            if membership.role:
                roles_by_user[membership.user_id].add(str(membership.role))
    return [
        {
            "id": str(row.id),
            "subject": row.subject,
            "email": row.email,
            "display_name": row.display_name,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "organization_count": len(organizations_by_user[row.id]),
            "membership_roles": sorted(roles_by_user[row.id])[:ROLE_CAP],
        }
        for row in rows
    ]


_USER_COLUMNS = (
    identities.c.id,
    identities.c.subject,
    identities.c.email,
    identities.c.display_name,
    identities.c.created_at,
    identities.c.updated_at,
)


def read_users(
    connection: Connection,
    *,
    query: str | None,
    email: str | None,
    registered_from: datetime | None,
    registered_to: datetime | None,
    limit: int,
    offset: int,
) -> list[object]:
    conditions: list[Any] = []
    if query:
        pattern = like_pattern(query)
        conditions.append(
            func.coalesce(identities.c.display_name, "").ilike(pattern, escape="\\")
            | func.coalesce(identities.c.email, "").ilike(pattern, escape="\\")
            | identities.c.subject.ilike(pattern, escape="\\")
        )
    if email:
        conditions.append(func.coalesce(identities.c.email, "").ilike(like_pattern(email), escape="\\"))
    if registered_from:
        conditions.append(identities.c.created_at >= registered_from)
    if registered_to:
        conditions.append(identities.c.created_at <= registered_to)
    total = _count(connection, identities, *conditions)
    rows = list(
        connection.execute(
            select(*_USER_COLUMNS)
            .where(*conditions)
            .order_by(identities.c.created_at.desc(), identities.c.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return [_user_items(connection, rows), total]


def read_user(connection: Connection, *, user_id: UUID, now: datetime) -> dict[str, object] | None:
    row = connection.execute(select(*_USER_COLUMNS).where(identities.c.id == user_id)).first()
    if row is None:
        return None
    month = now - timedelta(days=30)
    membership_rows = connection.execute(
        select(memberships.c.organization_id, organizations.c.name, memberships.c.role, memberships.c.created_at)
        .join(organizations, organizations.c.id == memberships.c.organization_id)
        .where(memberships.c.user_id == user_id)
        .order_by(organizations.c.name, memberships.c.created_at)
        .limit(MEMBERSHIP_CAP)
    )
    facts = _values(
        connection,
        organizations_created_30d=_n(organizations, organizations.c.created_by == user_id, organizations.c.created_at >= month),
        conversations_started_30d=_n(threads, threads.c.created_by == user_id, threads.c.created_at >= month),
        result_exports_30d=_n(exports, exports.c.exported_by == user_id, exports.c.created_at >= month),
        approval_decisions_30d=_n(approvals, approvals.c.decided_by == user_id, approvals.c.updated_at >= month),
        latest_organization=select(func.max(organizations.c.created_at)).where(organizations.c.created_by == user_id).scalar_subquery(),
        latest_thread=select(func.max(threads.c.created_at)).where(threads.c.created_by == user_id).scalar_subquery(),
        latest_export=select(func.max(exports.c.created_at)).where(exports.c.exported_by == user_id).scalar_subquery(),
        latest_decision=select(func.max(approvals.c.updated_at)).where(approvals.c.decided_by == user_id).scalar_subquery(),
        # Existence only: the credential row's hash column is never selected.
        password=exists().where(local_credentials.c.user_id == user_id),
    )
    latest = [facts[name] for name in ("latest_organization", "latest_thread", "latest_export", "latest_decision")]
    present = [value if isinstance(value, datetime) else datetime.fromisoformat(str(value)) for value in latest if value is not None]
    return {
        "user": _user_items(connection, [row])[0],
        "memberships": [
            {
                "organization_id": str(item.organization_id),
                "organization_name": item.name,
                "role": item.role,
                "joined_at": iso(item.created_at),
            }
            for item in membership_rows
        ],
        "activity": {
            **_ints({name: facts[name] for name in (
                "organizations_created_30d", "conversations_started_30d", "result_exports_30d", "approval_decisions_30d"
            )}),
            "last_activity_at": iso_or_none(max(present, key=lambda value: iso(value)) if present else None),
        },
        "local_password_configured": bool(facts["password"]),
    }


# ---------- organisations ----------

def _organization_select(now: datetime) -> Any:
    """Organisation columns plus correlated counts: one round trip for a whole page."""
    month = now - timedelta(days=30)
    return select(
        *_ORGANIZATION_COLUMNS,
        _n(memberships, memberships.c.organization_id == organizations.c.id).label("member_count"),
        _n(workers, workers.c.organization_id == organizations.c.id).label("worker_count"),
        _n(jobs, jobs.c.organization_id == organizations.c.id).label("job_count"),
        _n(runs, runs.c.organization_id == organizations.c.id, runs.c.created_at >= month).label("runs_30d"),
    )


def _organization_item(row: Any) -> dict[str, object]:
    return {
        "id": str(row.id),
        "name": row.name,
        "created_by": str(row.created_by),
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        "member_count": int(row.member_count or 0),
        "worker_count": int(row.worker_count or 0),
        "job_count": int(row.job_count or 0),
        "runs_30d": int(row.runs_30d or 0),
    }


_ORGANIZATION_COLUMNS = (
    organizations.c.id,
    organizations.c.name,
    organizations.c.created_by,
    organizations.c.created_at,
    organizations.c.updated_at,
)


def read_organizations(
    connection: Connection, *, query: str | None, limit: int, offset: int, now: datetime
) -> list[object]:
    conditions = [organizations.c.name.ilike(like_pattern(query), escape="\\")] if query else []
    total = _count(connection, organizations, *conditions)
    rows = connection.execute(
        _organization_select(now)
        .where(*conditions)
        .order_by(organizations.c.created_at.desc(), organizations.c.id)
        .limit(limit)
        .offset(offset)
    )
    return [[_organization_item(row) for row in rows], total]


def organization_state(*, open_incidents: int, unresolved_queue_failures: int, failed_runs_24h: int) -> str:
    if open_incidents > 0:
        return "attention"
    if unresolved_queue_failures > 0 or failed_runs_24h > 0:
        return "degraded"
    return "healthy"


def read_organization(connection: Connection, *, organization_id: UUID, now: datetime) -> dict[str, object] | None:
    row = connection.execute(_organization_select(now).where(organizations.c.id == organization_id)).first()
    if row is None:
        return None
    month, day = now - timedelta(days=30), now - timedelta(hours=24)
    scoped = organization_id
    member_rows = connection.execute(
        select(memberships.c.user_id, identities.c.email, identities.c.display_name, memberships.c.role, memberships.c.created_at)
        .join(identities, identities.c.id == memberships.c.user_id)
        .where(memberships.c.organization_id == scoped)
        .order_by(memberships.c.created_at, identities.c.email)
        .limit(MEMBER_CAP)
    )
    worker_rows = connection.execute(
        select(workers.c.id, workers.c.name, workers.c.department, workers.c.status, workers.c.created_at, workers.c.updated_at)
        .where(workers.c.organization_id == scoped)
        .order_by(workers.c.created_at.desc(), workers.c.id)
        .limit(WORKER_CAP)
    )
    job_rows = connection.execute(
        select(jobs.c.id, jobs.c.worker_id, jobs.c.name, jobs.c.status, jobs.c.current_revision, jobs.c.created_at, jobs.c.updated_at)
        .where(jobs.c.organization_id == scoped)
        .order_by(jobs.c.created_at.desc(), jobs.c.id)
        .limit(JOB_CAP)
    )
    # Never select integrations.config: it can reference credentials.
    integration_rows = connection.execute(
        select(
            integrations.c.id,
            integrations.c.provider,
            integrations.c.display_name,
            integrations.c.status,
            integrations.c.created_at,
            integrations.c.updated_at,
        )
        .where(integrations.c.organization_id == scoped)
        .order_by(integrations.c.display_name, integrations.c.id)
        .limit(INTEGRATION_CAP)
    )
    incident_rows = connection.execute(
        select(incidents.c.id, incidents.c.status, incidents.c.severity, incidents.c.summary, incidents.c.created_at, incidents.c.updated_at)
        .where(incidents.c.organization_id == scoped)
        .order_by(incidents.c.created_at.desc(), incidents.c.id)
        .limit(INCIDENT_CAP)
    )
    counts = _ints(
        _values(
            connection,
            open_incidents=_n(incidents, incidents.c.organization_id == scoped, _open_incident(incidents)),
            unresolved=_n(queue_failures, queue_failures.c.organization_id == scoped, queue_failures.c.resolved_at.is_(None)),
            failed_24h=_n(runs, runs.c.organization_id == scoped, runs.c.created_at >= day, _failed(runs.c.status)),
            runs_30d=_n(runs, runs.c.organization_id == scoped, runs.c.created_at >= month),
            failed_runs_30d=_n(runs, runs.c.organization_id == scoped, runs.c.created_at >= month, _failed(runs.c.status)),
            ai_invocations_30d=_n(ai_invocations, ai_invocations.c.organization_id == scoped, ai_invocations.c.created_at >= month),
            events_30d=_n(audit_events, audit_events.c.organization_id == scoped, audit_events.c.created_at >= month),
            high_or_critical_30d=_n(
                audit_events,
                audit_events.c.organization_id == scoped,
                audit_events.c.created_at >= month,
                func.lower(audit_events.c.severity).in_(("high", "critical")),
            ),
        )
    )
    open_incidents, unresolved, failed_24h = counts["open_incidents"], counts["unresolved"], counts["failed_24h"]
    return {
        "organization": _organization_item(row),
        "members": [
            {
                "user_id": str(item.user_id),
                "email": item.email,
                "display_name": item.display_name,
                "role": item.role,
                "joined_at": iso(item.created_at),
            }
            for item in member_rows
        ],
        "workers": [
            {
                "id": str(item.id),
                "name": item.name,
                "department": item.department,
                "status": item.status,
                "created_at": iso(item.created_at),
                "updated_at": iso(item.updated_at),
            }
            for item in worker_rows
        ],
        "jobs": [
            {
                "id": str(item.id),
                "worker_id": str(item.worker_id),
                "name": item.name,
                "status": item.status,
                "current_revision": int(item.current_revision),
                "created_at": iso(item.created_at),
                "updated_at": iso(item.updated_at),
            }
            for item in job_rows
        ],
        "integrations": [
            {
                "id": str(item.id),
                "provider": item.provider,
                "display_name": item.display_name,
                "status": item.status,
                "created_at": iso(item.created_at),
                "updated_at": iso(item.updated_at),
            }
            for item in integration_rows
        ],
        "incidents": [
            {
                "id": str(item.id),
                "status": item.status,
                "severity": item.severity,
                "summary": clip(item.summary, SUMMARY_CAP),
                "created_at": iso(item.created_at),
                "updated_at": iso(item.updated_at),
            }
            for item in incident_rows
        ],
        "usage": {name: counts[name] for name in ("runs_30d", "failed_runs_30d", "ai_invocations_30d")},
        "operational_state": {
            "state": organization_state(
                open_incidents=open_incidents, unresolved_queue_failures=unresolved, failed_runs_24h=failed_24h
            ),
            "open_incidents": open_incidents,
            "unresolved_queue_failures": unresolved,
            "failed_runs_24h": failed_24h,
        },
        "audit_summary": {name: counts[name] for name in ("events_30d", "high_or_critical_30d")},
    }
