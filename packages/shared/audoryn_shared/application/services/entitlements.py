"""Plan limits (commercial plan, phase 2): the one place that answers "may this organisation do
this now?" from the plan the Console sent, and counts what the organisation uses each period.

Nothing commercial is decided here: plans, their limits, statuses and periods are Console data.
With ENTITLEMENTS_ENFORCEMENT off (the default) no check reads or refuses anything. Usage is
counted whenever an organisation has a plan, so real usage is known before enforcement starts.

Only new work is checked: a task already running is never stopped by a limit.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.infrastructure.database.models import (
    Artifact,
    EntitlementSnapshot,
    EntitlementTemplate,
    Integration,
    Job,
    JobRevision,
    Organization,
    OrganizationEntitlement,
    OrganizationMembership,
    OrganizationUsage,
    Worker,
)

# A plan changes rarely; each process re-reads it at most this often (and at once after it
# applies a new one itself).
CACHE_SECONDS = 30.0
NEW_WORK_STATUSES = frozenset({"trialing", "active", "past_due"})
# What a new task needs room in. The AI allowance is checked when work starts, never during it.
TASK_ALLOWANCES = ("tasks.monthly", "ai.tokens.monthly")
Status = Literal["trialing", "active", "past_due", "paused", "cancelled", "expired"]

# What each limit is called in messages.
_NAMES = {
    "workers.max": "workers", "jobs.per_worker.max": "standing jobs per worker", "members.max": "members",
    "connections.max": "connections", "tasks.monthly": "tasks", "ai.tokens.monthly": "AI tokens",
    "coding.seconds.monthly": "seconds of coding time", "web_research.monthly": "web searches",
    "storage.bytes": "bytes of storage", "browser.enabled": "browser work", "coding.enabled": "coding work",
    "events.enabled": "reacting to events", "browser.sessions.concurrent": "browser sessions at once",
    "schedules.min_interval_minutes": "minutes between scheduled runs, at least",
}
# What the workspace's "Usage & billing" page shows, in order: what it has, then this period's use.
_SHOWN = ("workers.max", "jobs.per_worker.max", "members.max", "connections.max", "storage.bytes",
          "tasks.monthly", "ai.tokens.monthly", "coding.seconds.monthly", "web_research.monthly")
_INCLUDED = ("browser.enabled", "coding.enabled", "events.enabled")
_SETTINGS = ("browser.sessions.concurrent", "schedules.min_interval_minutes")
# Share of a limit at which the page warns.
NEAR_LIMIT = 0.8
# Period allowances: the usage counter each one is measured against.
_ALLOWANCES = {
    "tasks.monthly": OrganizationUsage.tasks, "ai.tokens.monthly": OrganizationUsage.ai_tokens,
    "coding.seconds.monthly": OrganizationUsage.coding_seconds,
    "web_research.monthly": OrganizationUsage.web_research,
}


class EntitlementDenied(HTTPException):
    """New work the organisation's plan does not allow now; the message says what to do. The
    `X-Plan-Limit` header names the limit (or "plan.status"), so the app can show the meter."""

    def __init__(self, message: str, limit: str) -> None:
        super().__init__(status_code=402, detail=message, headers={"X-Plan-Limit": limit})
        self.message = message
        self.limit = limit


def _limit_value(value: Any) -> bool | int:
    if isinstance(value, bool) or (isinstance(value, int) and value >= 0):
        return value
    raise ValueError("an entitlement is true, false or a whole number of at least 0")


class _PlanFields(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    version: int = Field(ge=1)
    status: Status
    plan_code: str | None = Field(default=None, alias="planCode", max_length=64)
    plan_name: str | None = Field(default=None, alias="planName", max_length=120)
    entitlements: dict[str, bool | int]

    @field_validator("entitlements", mode="before")
    @classmethod
    def _values(cls, values: Any) -> dict[str, bool | int]:
        if not isinstance(values, dict):
            raise ValueError("entitlements is an object of named limits")  # noqa: TRY004 - pydantic reports ValueError
        return {str(key): _limit_value(value) for key, value in values.items()}


class SnapshotDocument(_PlanFields):
    """One organisation's plan as the Console sends it."""
    organization_id: UUID = Field(alias="organizationId")
    period_start: datetime = Field(alias="periodStart")
    period_end: datetime | None = Field(default=None, alias="periodEnd")
    valid_until: datetime | None = Field(default=None, alias="validUntil")


class TemplateDocument(_PlanFields):
    """The plan a new workspace starts on. Usage resets every `period_days`; the plan ends after
    `valid_days` (a trial), or never when that is not set (a free plan)."""
    period_days: int = Field(alias="periodDays", ge=1, le=366)
    valid_days: int | None = Field(default=None, alias="validDays", ge=1, le=366)


@dataclass(frozen=True, slots=True)
class Plan:
    version: int
    status: str
    period_start: datetime
    period_end: datetime | None
    valid_until: datetime | None
    values: dict[str, Any]

    def period_at(self, now: datetime) -> tuple[datetime, datetime | None]:
        """The usage period `now` falls in. Once the Console's period has ended, the next periods
        of the same length follow until it sends a newer plan, so usage still resets."""
        if self.period_end is None or now < self.period_end or self.period_end <= self.period_start:
            return self.period_start, self.period_end
        length = self.period_end - self.period_start
        start = self.period_start + length * ((now - self.period_start) // length)
        return start, start + length

    def allows_new_work(self, now: datetime) -> bool:
        return self.status in NEW_WORK_STATUSES and (self.valid_until is None or now < self.valid_until)

    def number(self, key: str) -> int:
        """A limit; one the plan does not name is 0 (never unlimited)."""
        value = self.values.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def includes(self, key: str) -> bool:
        return self.values.get(key) is True


_cache: dict[UUID, tuple[float, Plan | None]] = {}


def forget(organization_id: UUID | None = None) -> None:
    if organization_id is None:
        _cache.clear()
    else:
        _cache.pop(organization_id, None)


@asynccontextmanager
async def _session(session: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    if session is not None:
        yield session
        return
    from audoryn_shared.infrastructure.database.session import session_factory

    async with session_factory() as own:
        yield own


async def current(session: AsyncSession | None, organization_id: UUID) -> Plan | None:
    cached = _cache.get(organization_id)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    async with _session(session) as db:
        row = await db.get(OrganizationEntitlement, organization_id, populate_existing=True)
    plan = None if row is None else Plan(row.version, row.status, row.period_start, row.period_end,
                                         row.valid_until, dict(row.entitlements or {}))
    _cache[organization_id] = (time.monotonic() + CACHE_SECONDS, plan)
    return plan


def _status_message(plan: Plan | None) -> str:
    if plan is None:
        return "This workspace has no plan yet, so new work can't start. Ask your administrator to choose a plan."
    state = "ended" if plan.status in NEW_WORK_STATUSES else plan.status.replace("_", " ")
    return (f"This workspace's plan is {state}, so new work can't start. Running tasks finish and your "
            "results stay available. Ask your administrator to renew the plan.")


def _over(key: str, limit: int) -> EntitlementDenied:
    return EntitlementDenied(f"Your plan includes {limit:,} {_NAMES.get(key, key)}. "
                             "Ask your administrator to upgrade the plan.", key)


async def require_new_work(session: AsyncSession | None, organization_id: UUID) -> Plan | None:
    """The plan when new work may start; None when enforcement is off."""
    if not settings.entitlements_enforcement:
        return None
    plan = await current(session, organization_id)
    if plan is None or not plan.allows_new_work(datetime.now(UTC)):
        raise EntitlementDenied(_status_message(plan), "plan.status")
    return plan


def _standing_jobs(organization_id: UUID) -> Any:
    """Counts standing jobs only: a one-time task's job ends with its task."""
    one_shot = JobRevision.definition["autonomy"]["oneShot"].as_boolean()
    return (select(func.count()).select_from(Job)
            .join(JobRevision, (JobRevision.job_id == Job.id) & (JobRevision.revision == Job.current_revision))
            .where(Job.organization_id == organization_id, Job.status != "archived",
                   func.coalesce(one_shot, False).is_(False)))


async def _count(session: AsyncSession, organization_id: UUID, key: str, worker_id: UUID | None) -> int:
    if key == "workers.max":
        query = select(func.count()).select_from(Worker).where(
            Worker.organization_id == organization_id, Worker.status != "archived")
    elif key == "members.max":
        query = select(func.count()).select_from(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id)
    elif key == "connections.max":
        query = select(func.count()).select_from(Integration).where(
            Integration.organization_id == organization_id, Integration.status != "disconnected",
            Integration.provider != "web_research")
    elif key == "jobs.per_worker.max":
        query = _standing_jobs(organization_id).where(Job.worker_id == worker_id)
    elif key == "storage.bytes":
        query = select(func.coalesce(func.sum(Artifact.size_bytes), 0)).where(Artifact.organization_id == organization_id)
    else:
        raise ValueError(f"No count for {key}.")
    return int(await session.scalar(query) or 0)


async def require_room(session: AsyncSession, organization_id: UUID, key: str, *, worker_id: UUID | None = None,
                       adding: int = 1) -> None:
    """One more of something the plan counts (workers, members, connections, a worker's standing
    jobs, stored bytes) fits within the plan."""
    plan = await require_new_work(session, organization_id)
    if plan is not None and await _count(session, organization_id, key, worker_id) + adding > plan.number(key):
        raise _over(key, plan.number(key))


async def require_feature(session: AsyncSession | None, organization_id: UUID, key: str) -> None:
    plan = await require_new_work(session, organization_id)
    if plan is not None and not plan.includes(key):
        raise EntitlementDenied(f"Your plan does not include {_NAMES.get(key, key)}. "
                                "Ask your administrator to upgrade the plan.", key)


async def require_allowance(session: AsyncSession | None, organization_id: UUID, *keys: str) -> Plan | None:
    """Each period allowance still has room; checked when new work starts, never during it."""
    plan = await require_new_work(session, organization_id)
    if plan is None:
        return None
    start, end = plan.period_at(datetime.now(UTC))
    async with _session(session) as db:
        used = (await db.execute(select(*(_ALLOWANCES[key] for key in keys)).where(
            OrganizationUsage.organization_id == organization_id,
            OrganizationUsage.period_start == start))).first()
    for index, key in enumerate(keys):
        if (used[index] if used is not None else 0) >= plan.number(key):
            renews = f" It renews on {end:%d %B %Y}." if end else ""
            raise EntitlementDenied(f"This period's {_NAMES[key]} ({plan.number(key):,}) are used up.{renews} "
                                    "Ask your administrator to upgrade the plan.", key)
    return plan


async def plan_number(session: AsyncSession | None, organization_id: UUID, key: str) -> int | None:
    """A numeric setting of the plan (a minimum interval, a concurrency); None when enforcement is off."""
    plan = await require_new_work(session, organization_id)
    return None if plan is None else plan.number(key)


async def count_usage(session: AsyncSession | None, organization_id: UUID, *, tasks: int = 0, ai_tokens: int = 0,
                      browser_seconds: float = 0.0, coding_seconds: float = 0.0, web_research: int = 0) -> None:
    """Add to the organisation's usage this period; nothing when it has no plan. Joins the caller's
    transaction when given a session, otherwise commits its own."""
    amounts = {"tasks": tasks, "ai_tokens": ai_tokens, "browser_seconds": browser_seconds,
               "coding_seconds": coding_seconds, "web_research": web_research}
    amounts = {name: value for name, value in amounts.items() if value}
    if not amounts:
        return
    async with _session(session) as db:
        plan = await current(db, organization_id)
        if plan is None:
            return
        now = datetime.now(UTC)
        key = {"organization_id": organization_id, "period_start": plan.period_at(now)[0]}
        changed = await db.execute(
            update(OrganizationUsage).where(*(getattr(OrganizationUsage, name) == value for name, value in key.items()))
            .values(updated_at=now, **{name: getattr(OrganizationUsage, name) + value for name, value in amounts.items()}))
        if not getattr(changed, "rowcount", 0):
            await _insert_usage(db, {**key, **amounts, "updated_at": now})
        if session is None:
            await db.commit()


async def _insert_usage(db: AsyncSession, values: dict[str, Any]) -> None:
    """The period's first usage. A concurrent first insert wins; this one is then added to it."""
    from sqlalchemy.dialects import postgresql, sqlite

    dialect = postgresql if db.get_bind().dialect.name == "postgresql" else sqlite
    statement = dialect.insert(OrganizationUsage).values(**values)
    added = {name: getattr(statement.excluded, name) + getattr(OrganizationUsage, name)
             for name in values if name not in {"organization_id", "period_start", "updated_at"}}
    await db.execute(statement.on_conflict_do_update(
        index_elements=["organization_id", "period_start"], set_={**added, "updated_at": values["updated_at"]}))


async def apply_snapshot(session: AsyncSession, document: SnapshotDocument) -> str:
    """Apply one organisation's plan in the caller's transaction: "applied", "already_applied"
    (this version is current) or "stale" (a newer version is current)."""
    row = await session.get(OrganizationEntitlement, document.organization_id, with_for_update=True)
    if row is not None and row.version >= document.version:
        return "already_applied" if row.version == document.version else "stale"
    values = {"version": document.version, "status": document.status, "plan_code": document.plan_code,
              "period_start": document.period_start, "period_end": document.period_end,
              "valid_until": document.valid_until, "entitlements": document.entitlements,
              "received_at": datetime.now(UTC)}
    await _apply(session, row, document.organization_id, values, document.model_dump(mode="json", by_alias=True))
    await session.flush()
    forget(document.organization_id)
    return "applied"


async def apply_template(session: AsyncSession, document: TemplateDocument) -> str:
    """Keep the newest template. Workspaces that have no plan yet (those created before the first
    template, at the cutover) start on it now."""
    newest = await session.scalar(select(func.max(EntitlementTemplate.version)))
    if newest is not None and newest >= document.version:
        return "already_applied" if newest == document.version else "stale"
    stored = document.model_dump(mode="json", by_alias=True)
    session.add(EntitlementTemplate(version=document.version, document=stored))
    unplanned = await session.scalars(select(Organization.id).where(
        ~exists().where(OrganizationEntitlement.organization_id == Organization.id)))
    for organization_id in unplanned.all():
        await _start(session, organization_id, document, stored)
    await session.flush()
    return "applied"


async def start_from_template(session: AsyncSession, organization_id: UUID) -> None:
    """A new workspace starts on the template plan (free, or a trial), so it can work even before
    the Console sends its own plan."""
    stored = await session.scalar(select(EntitlementTemplate.document).order_by(EntitlementTemplate.version.desc()).limit(1))
    if stored is not None:
        await _start(session, organization_id, TemplateDocument.model_validate(stored), stored)


async def _start(session: AsyncSession, organization_id: UUID, template: TemplateDocument,
                 stored: dict[str, Any]) -> None:
    """Version 0 of an organisation's plan, from the template: the Console's first version replaces it."""
    start = datetime.now(UTC)
    valid_until = start + timedelta(days=template.valid_days) if template.valid_days else None
    await _apply(session, None, organization_id, {
        "version": 0, "status": template.status, "plan_code": template.plan_code, "period_start": start,
        "period_end": start + timedelta(days=template.period_days), "valid_until": valid_until,
        "entitlements": template.entitlements, "received_at": start,
    }, {**stored, "fromTemplate": template.version})
    forget(organization_id)


def limit_name(key: str) -> str:
    """What a limit is called in messages ("plan.status" is the plan itself)."""
    return _NAMES.get(key, "the plan")


def comparison() -> list[dict[str, str]]:
    """The rows of the Plans page's comparison, named and ordered as on the usage page."""
    return ([{"key": key, "name": _NAMES[key], "kind": "period" if key in _ALLOWANCES else "count"} for key in _SHOWN]
            + [{"key": key, "name": _NAMES[key], "kind": "feature"} for key in _INCLUDED]
            + [{"key": key, "name": _NAMES[key], "kind": "setting"} for key in _SETTINGS])


def _limit(key: str, used: float, limit: int, *, period: bool) -> dict[str, Any]:
    share = used / limit if limit else 1.0
    state = "reached" if used >= limit else "near" if share >= NEAR_LIMIT else "ok"
    return {"key": key, "name": _NAMES[key], "used": used, "limit": limit, "period": period, "state": state}


async def summary(session: AsyncSession, organization_id: UUID) -> dict[str, Any] | None:
    """The organisation's plan and what it uses of it, for its "Usage & billing" page; None
    without a plan. Reads the usage counters, never the work itself."""
    plan = await current(session, organization_id)
    if plan is None:
        return None
    now = datetime.now(UTC)
    start, end = plan.period_at(now)
    document = await session.scalar(select(EntitlementSnapshot.document).where(
        EntitlementSnapshot.organization_id == organization_id, EntitlementSnapshot.version == plan.version)) or {}
    period = (await session.execute(select(*_ALLOWANCES.values()).where(
        OrganizationUsage.organization_id == organization_id, OrganizationUsage.period_start == start))).first()
    used: dict[str, float] = {key: (period[index] if period else 0) for index, key in enumerate(_ALLOWANCES)}
    for key in ("workers.max", "members.max", "connections.max", "storage.bytes"):
        used[key] = await _count(session, organization_id, key, None)
    # A worker's standing jobs: the busiest worker is the one nearest the limit.
    used["jobs.per_worker.max"] = max((await session.scalars(
        _standing_jobs(organization_id).group_by(Job.worker_id))).all(), default=0)
    return {
        "code": document.get("planCode"), "name": document.get("planName"), "version": plan.version,
        "status": plan.status, "allowsNewWork": plan.allows_new_work(now), "enforced": settings.entitlements_enforcement,
        "periodStart": start.isoformat(), "periodEnd": end.isoformat() if end else None,
        "validUntil": plan.valid_until.isoformat() if plan.valid_until else None,
        "limits": [_limit(key, used[key], plan.number(key), period=key in _ALLOWANCES) for key in _SHOWN],
        "included": [{"key": key, "name": _NAMES[key], "included": plan.includes(key)} for key in _INCLUDED],
        "settings": [{"key": key, "name": _NAMES[key], "value": plan.number(key)} for key in _SETTINGS],
    }


async def _apply(session: AsyncSession, row: OrganizationEntitlement | None, organization_id: UUID,
                 values: dict[str, Any], document: dict[str, Any]) -> None:
    if row is None:
        session.add(OrganizationEntitlement(organization_id=organization_id, **values))
    else:
        for name, value in values.items():
            setattr(row, name, value)
    # The history keeps the first copy of each version (a plan re-applied after being lost).
    if await session.scalar(select(EntitlementSnapshot.id).where(
            EntitlementSnapshot.organization_id == organization_id,
            EntitlementSnapshot.version == values["version"])) is None:
        session.add(EntitlementSnapshot(organization_id=organization_id, version=values["version"], document=document))
