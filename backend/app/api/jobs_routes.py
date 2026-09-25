from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import append_audit, not_found, require_permission, utcnow
from app.api.workforce_routes import automatic_agent, create_worker
from app.domain.identity.principals import HumanPrincipal
from app.execution.bootstrap import execution_provider_registry
from app.infrastructure.database.models import (
    Action,
    Approval,
    Artifact,
    CapabilityProfile,
    Job,
    JobRevision,
    Policy,
    PolicyRevision,
    Result,
    ResultExport,
    ResultVersion,
    Run,
    RunStep,
    Worker,
    WorkItem,
)
from app.infrastructure.database.session import database_session
from app.runtime.qstash_trigger import request_runtime_execution

v1_router = APIRouter(tags=["jobs", "scheduler"])
v2_router = APIRouter(tags=["jobs", "scheduler"])

_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _schedule_zone(schedule: dict[str, Any]) -> ZoneInfo:
    raw = str(schedule.get("timezone") or "UTC")
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _local_clock(schedule: dict[str, Any]) -> tuple[int, int]:
    raw = str(schedule.get("localTime") or "09:00")
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour = min(23, max(0, int(hour_text)))
        minute = min(59, max(0, int(minute_text)))
    except (TypeError, ValueError):
        return (9, 0)
    return (hour, minute)


def _next_schedule_occurrence(
    schedule: dict[str, Any],
    after: datetime,
) -> datetime:
    after = after.astimezone(UTC)
    cadence = str(schedule.get("cadence") or "daily")
    if cadence == "interval":
        minutes = min(10080, max(5, int(schedule.get("intervalMinutes") or 60)))
        return after + timedelta(minutes=minutes)

    zone = _schedule_zone(schedule)
    local_after = after.astimezone(zone)
    hour, minute = _local_clock(schedule)
    allowed_days = {
        _WEEKDAY_INDEX[value]
        for value in schedule.get("weekdays", [])
        if value in _WEEKDAY_INDEX
    }
    if cadence != "weekly" or not allowed_days:
        allowed_days = set(range(7))

    for offset in range(8):
        day = local_after.date() + timedelta(days=offset)
        candidate = datetime(
            day.year,
            day.month,
            day.day,
            hour,
            minute,
            tzinfo=zone,
        )
        if candidate.weekday() not in allowed_days:
            continue
        if candidate <= local_after:
            continue
        return candidate.astimezone(UTC)
    return (local_after + timedelta(days=1)).astimezone(UTC)


def _quick_trigger_payload(timing: dict[str, Any]) -> dict[str, Any]:
    mode = str(timing.get("mode") or "manual")
    timezone = str(timing.get("timezone") or "UTC")
    schedule = {
        "enabled": mode in {"interval", "daily", "weekly", "custom"},
        "cadence": (
            "interval"
            if mode == "interval"
            else "weekly"
            if mode in {"weekly", "custom"}
            else "daily"
        ),
        "timezone": timezone,
        "localTime": str(timing.get("localTime") or "09:00"),
        "weekdays": list(
            timing.get(
                "weekdays",
                ["monday", "tuesday", "wednesday", "thursday", "friday"],
            )
        ),
        "intervalMinutes": int(timing.get("intervalMinutes") or 60),
        "missedRunPolicy": "queue_once",
        "outsideWorkingHoursPolicy": "next_open",
    }
    return {
        "schedule": schedule,
        "apiEnabled": False,
        "internalEventKeys": (
            [str(timing.get("eventKey"))]
            if mode == "event" and timing.get("eventKey")
            else []
        ),
        "dependencyJobIds": (
            list(timing.get("dependencyJobIds", []))
            if mode == "dependency"
            else []
        ),
    }


def _system_jobs_principal(organization_id: UUID) -> HumanPrincipal:
    zero = UUID(int=0)
    return HumanPrincipal(
        user_id=zero,
        organization_id=organization_id,
        membership_id=zero,
        role="system",
        permissions=frozenset({"jobs.run"}),
    )


def _next_worker_open(worker: Worker, when: datetime) -> datetime:
    profile = worker.profile if isinstance(worker.profile, dict) else {}
    raw_hours = profile.get("workingHours")
    hours = raw_hours if isinstance(raw_hours, dict) else {}
    zone_name = str(hours.get("timezone") or "UTC")
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    days = hours.get("days")
    days = days if isinstance(days, dict) else {}
    local = when.astimezone(zone)
    names = tuple(_WEEKDAY_INDEX)

    for offset in range(8):
        day_date = local.date() + timedelta(days=offset)
        name = names[day_date.weekday()]
        raw = days.get(name)
        day = raw if isinstance(raw, dict) else {}
        if not bool(day.get("enabled", offset < 5)):
            continue
        start_raw = str(day.get("start") or "09:00")
        end_raw = str(day.get("end") or "17:00")
        try:
            start_h, start_m = (int(part) for part in start_raw.split(":", 1))
            end_h, end_m = (int(part) for part in end_raw.split(":", 1))
        except (TypeError, ValueError):
            start_h, start_m, end_h, end_m = 9, 0, 17, 0
        opening = datetime(
            day_date.year,
            day_date.month,
            day_date.day,
            start_h,
            start_m,
            tzinfo=zone,
        )
        closing = datetime(
            day_date.year,
            day_date.month,
            day_date.day,
            end_h,
            end_m,
            tzinfo=zone,
        )
        if offset == 0 and opening <= local <= closing:
            return when
        if opening > local:
            return opening.astimezone(UTC)
    return when


async def queue_due_schedules(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int = 50,
) -> int:
    jobs = list(
        (
            await session.scalars(
                select(Job)
                .where(Job.status == "active")
                .order_by(Job.updated_at, Job.id)
                .limit(max(limit * 4, 50))
            )
        ).all()
    )
    queued = 0
    for job in jobs:
        if queued >= limit:
            break
        revision = await current_revision(session, job)
        definition = (
            dict(revision.definition)
            if isinstance(revision.definition, dict)
            else {}
        )
        raw_config = definition.get("triggerConfig")
        config = dict(raw_config) if isinstance(raw_config, dict) else {}
        raw_schedule = config.get("schedule")
        schedule = dict(raw_schedule) if isinstance(raw_schedule, dict) else {}
        if not bool(schedule.get("enabled")):
            continue

        due = _parse_timestamp(schedule.get("nextDueAt"))
        if due is None:
            anchor_time = (
                _parse_timestamp(config.get("updatedAt"))
                or _parse_timestamp(config.get("createdAt"))
                or job.updated_at
            )
            due = _next_schedule_occurrence(schedule, anchor_time)
        if due > now:
            continue

        next_due = _next_schedule_occurrence(schedule, due)
        if str(schedule.get("missedRunPolicy") or "queue_once") == "skip":
            while next_due <= now:
                next_due = _next_schedule_occurrence(schedule, next_due)
            schedule["nextDueAt"] = next_due.isoformat()
            config["schedule"] = schedule
            definition["triggerConfig"] = config
            revision.definition = definition
            await session.commit()
            continue

        worker = await session.get(Worker, job.worker_id)
        scheduled_for = due
        if (
            worker is not None
            and str(schedule.get("outsideWorkingHoursPolicy") or "next_open")
            == "next_open"
        ):
            scheduled_for = _next_worker_open(worker, due)

        schedule["nextDueAt"] = next_due.isoformat()
        config["schedule"] = schedule
        definition["triggerConfig"] = config
        revision.definition = definition
        trigger = {
            "type": "schedule",
            "requestedByType": "system",
            "requestedBy": "qstash-scheduler",
            "requestedAt": now.isoformat(),
            "key": None,
            "eventId": None,
            "payload": None,
            "scheduledFor": due.isoformat(),
            "dedupeKey": f"schedule:{config.get('id', job.id)}:{due.isoformat()}",
            "sourceJobId": None,
            "sourceWorkItemId": None,
            "configId": config.get("id"),
        }
        await queue_job(
            session,
            job.organization_id,
            job,
            _system_jobs_principal(job.organization_id),
            trigger=trigger,
            scheduled_at=scheduled_for,
        )
        queued += 1
    return queued


async def current_revision(session: AsyncSession, job: Job) -> JobRevision:
    revision = await session.scalar(
        select(JobRevision).where(
            JobRevision.job_id == job.id,
            JobRevision.revision == job.current_revision,
        )
    )
    if revision is None:
        raise HTTPException(500, "Job revision is unavailable.")
    return revision


def job_definition(
    payload: dict[str, Any],
    principal: HumanPrincipal,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous = previous or {}
    raw_integration_requirements = payload.get(
        "integrationRequirements",
        previous.get("integrationRequirements", []),
    )
    integration_requirements = (
        list(raw_integration_requirements)
        if isinstance(raw_integration_requirements, list)
        else []
    )
    raw_autonomy = payload.get("autonomy")
    if not isinstance(raw_autonomy, dict):
        raw_autonomy = previous.get("autonomy")
    autonomy: dict[str, Any] = (
        {str(key): value for key, value in raw_autonomy.items()}
        if isinstance(raw_autonomy, dict)
        else {}
    )
    return {
        "description": payload.get("description", previous.get("description", "")),
        "objective": payload.get("objective", previous.get("objective", "")),
        "instructions": payload.get("instructions", previous.get("instructions", "")),
        "responsibilityLinks": list(
            payload.get(
                "responsibilityLinks", previous.get("responsibilityLinks", [])
            )
        ),
        "requiredCapabilities": list(
            payload.get(
                "requiredCapabilities", previous.get("requiredCapabilities", [])
            )
        ),
        "priority": payload.get("priority", previous.get("priority", "normal")),
        "completionCriteria": list(
            payload.get(
                "completionCriteria", previous.get("completionCriteria", [])
            )
        ),
        "createdBy": previous.get("createdBy", str(principal.user_id)),
        "updatedBy": str(principal.user_id),
        "triggerConfig": previous.get("triggerConfig"),
        "integrationRequirements": integration_requirements,
        "autonomy": autonomy,
    }


async def create_job(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> Job:
    require_permission(principal, "jobs.manage")
    worker_id = UUID(str(payload.get("workerId")))
    worker = await session.scalar(
        select(Worker).where(
            Worker.organization_id == organization_id, Worker.id == worker_id
        )
    )
    if worker is None:
        raise not_found("Worker")
    name = str(payload.get("name", "")).strip()
    if len(name) < 2:
        raise HTTPException(400, "Job name must be at least 2 characters.")
    now = utcnow()
    job = Job(
        organization_id=organization_id,
        worker_id=worker_id,
        name=name,
        status="draft",
        current_revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.flush()
    session.add(
        JobRevision(
            job_id=job.id,
            revision=1,
            definition=job_definition(payload, principal),
            created_by=principal.user_id,
            created_at=now,
        )
    )
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="job.created",
        category="workforce",
        resource_type="job",
        resource_id=str(job.id),
        resource_name=job.name,
        outcome="draft",
        summary=f"Job {job.name} created.",
    )
    await session.commit()
    await session.refresh(job)
    return job


async def update_job(
    session: AsyncSession,
    organization_id: UUID,
    job_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> Job:
    require_permission(principal, "jobs.manage")
    job = await session.scalar(
        select(Job).where(Job.organization_id == organization_id, Job.id == job_id)
    )
    if job is None:
        raise not_found("Job")
    current = await current_revision(session, job)
    if payload.get("workerId"):
        job.worker_id = UUID(str(payload["workerId"]))
    if payload.get("name"):
        job.name = str(payload["name"]).strip() or job.name
    job.current_revision += 1
    now = utcnow()
    session.add(
        JobRevision(
            job_id=job.id,
            revision=job.current_revision,
            definition=job_definition(payload, principal, current.definition),
            created_by=principal.user_id,
            created_at=now,
        )
    )
    job.updated_at = now
    await session.commit()
    await session.refresh(job)
    return job


async def job_public(session: AsyncSession, job: Job) -> dict[str, Any]:
    revision = await current_revision(session, job)
    definition: dict[str, Any] = (
        dict(revision.definition) if isinstance(revision.definition, dict) else {}
    )
    raw_trigger = definition.get("triggerConfig")
    trigger: dict[str, Any] = (
        dict(raw_trigger) if isinstance(raw_trigger, dict) else {}
    )
    raw_integration_requirements = definition.get("integrationRequirements")
    integration_requirements = (
        list(raw_integration_requirements)
        if isinstance(raw_integration_requirements, list)
        else []
    )
    raw_autonomy = definition.get("autonomy")
    autonomy: dict[str, Any] = (
        {str(key): value for key, value in raw_autonomy.items()}
        if isinstance(raw_autonomy, dict)
        else {}
    )
    return {
        "id": str(job.id),
        "organizationId": str(job.organization_id),
        "workerId": str(job.worker_id),
        "name": job.name,
        "description": str(definition.get("description", "")),
        "objective": str(definition.get("objective", "")),
        "instructions": str(definition.get("instructions", "")),
        "responsibilityLinks": list(definition.get("responsibilityLinks", [])),
        "requiredCapabilities": list(definition.get("requiredCapabilities", [])),
        "integrationRequirements": integration_requirements,
        "autonomy": autonomy,
        "priority": str(definition.get("priority", "normal")),
        "completionCriteria": list(definition.get("completionCriteria", [])),
        "status": job.status,
        "triggerType": "manual",
        "scheduleId": trigger.get("id"),
        "revision": job.current_revision,
        "createdBy": str(definition.get("createdBy", revision.created_by)),
        "createdAt": job.created_at.isoformat(),
        "updatedBy": str(definition.get("updatedBy", revision.created_by)),
        "updatedAt": job.updated_at.isoformat(),
    }


async def job_v2(session: AsyncSession, job: Job) -> dict[str, Any]:
    public = await job_public(session, job)
    return {
        "identity": {
            "id": public["id"],
            "name": public["name"],
            "status": public["status"],
            "revision": public["revision"],
            "createdAt": public["createdAt"],
            "updatedAt": public["updatedAt"],
        },
        "organizationId": public["organizationId"],
        "assignment": {"workerId": public["workerId"]},
        "definition": {
            "description": public["description"],
            "objective": public["objective"],
            "instructions": public["instructions"],
            "responsibilityLinks": public["responsibilityLinks"],
            "requiredCapabilities": public["requiredCapabilities"],
            "integrationRequirements": public["integrationRequirements"],
            "autonomy": public["autonomy"],
            "priority": public["priority"],
            "completionCriteria": public["completionCriteria"],
        },
        "trigger": {
            "type": public["triggerType"],
            "scheduleId": public["scheduleId"],
        },
        "audit": {
            "createdBy": public["createdBy"],
            "updatedBy": public["updatedBy"],
        },
    }


async def work_item_public(
    session: AsyncSession, item: WorkItem
) -> dict[str, Any]:
    job = await session.get(Job, item.job_id)
    revision = await session.get(JobRevision, item.job_revision_id)
    definition = (
        revision.definition
        if revision is not None and isinstance(revision.definition, dict)
        else {}
    )
    payload = item.payload if isinstance(item.payload, dict) else {}
    run = await session.scalar(
        select(Run)
        .where(Run.work_item_id == item.id)
        .order_by(desc(Run.created_at))
        .limit(1)
    )
    trigger = payload.get("trigger")
    if not isinstance(trigger, dict):
        trigger = {
            "type": "manual",
            "requestedByType": "human",
            "requestedBy": str(payload.get("requestedBy", "")),
            "requestedAt": item.created_at.isoformat(),
            "key": None,
            "eventId": None,
            "payload": None,
            "scheduledFor": None,
            "dedupeKey": None,
            "sourceJobId": None,
            "sourceWorkItemId": None,
            "configId": None,
        }
    return {
        "id": str(item.id),
        "organizationId": str(item.organization_id),
        "workerId": str(job.worker_id) if job else "",
        "jobId": str(item.job_id),
        "jobRevision": revision.revision if revision else 1,
        "trigger": trigger,
        "priority": item.priority,
        "status": item.status,
        "scheduledAt": item.scheduled_at.isoformat(),
        "startedAt": payload.get("startedAt"),
        "completedAt": payload.get("completedAt"),
        "retryCount": int(payload.get("retryCount", 0)),
        "runId": str(run.id) if run else None,
        "correlationId": item.correlation_id,
        "snapshot": {
            "jobName": job.name if job else "",
            "objective": str(definition.get("objective", "")),
            "instructions": str(definition.get("instructions", "")),
            "responsibilityLinks": list(
                definition.get("responsibilityLinks", [])
            ),
            "requiredCapabilities": list(
                definition.get("requiredCapabilities", [])
            ),
            "completionCriteria": list(
                definition.get("completionCriteria", [])
            ),
            "priority": str(definition.get("priority", item.priority)),
        },
        "cancelledBy": payload.get("cancelledBy"),
        "cancelledAt": payload.get("cancelledAt"),
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
        "leaseExpiresAt": payload.get("leaseExpiresAt"),
        "lastError": payload.get("lastError"),
    }


async def work_item_v2(
    session: AsyncSession, item: WorkItem
) -> dict[str, Any]:
    public = await work_item_public(session, item)
    return {
        "identity": {
            "id": public["id"],
            "status": public["status"],
            "createdAt": public["createdAt"],
            "updatedAt": public["updatedAt"],
        },
        "organizationId": public["organizationId"],
        "assignment": {
            "workerId": public["workerId"],
            "jobId": public["jobId"],
            "jobRevision": public["jobRevision"],
        },
        "trigger": public["trigger"],
        "queue": {
            "priority": public["priority"],
            "scheduledAt": public["scheduledAt"],
            "startedAt": public["startedAt"],
            "completedAt": public["completedAt"],
            "retryCount": public["retryCount"],
        },
        "runtime": {
            "runId": public["runId"],
            "leaseExpiresAt": public["leaseExpiresAt"],
            "lastError": public["lastError"],
        },
        "trace": {"correlationId": public["correlationId"]},
        "snapshot": public["snapshot"],
        "cancellation": {
            "cancelledBy": public["cancelledBy"],
            "cancelledAt": public["cancelledAt"],
        },
    }


async def queue_job(
    session: AsyncSession,
    organization_id: UUID,
    job: Job,
    principal: HumanPrincipal,
    *,
    trigger: dict[str, Any] | None = None,
    scheduled_at: datetime | None = None,
) -> WorkItem:
    require_permission(principal, "jobs.run")
    revision = await current_revision(session, job)
    definition = revision.definition if isinstance(revision.definition, dict) else {}
    now = utcnow()
    dedupe_key = (
        str(trigger.get("dedupeKey"))
        if trigger is not None and trigger.get("dedupeKey")
        else None
    )
    idempotency_key = dedupe_key or f"queue:{job.id}:{uuid4()}"
    if dedupe_key:
        existing = await session.scalar(
            select(WorkItem).where(
                WorkItem.organization_id == organization_id,
                WorkItem.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            await request_runtime_execution(
                organization_id=organization_id,
                work_item_id=existing.id,
                expected_step=0,
                reason="queue-dedupe",
            )
            return existing

    correlation_id = str(uuid4())
    item = WorkItem(
        organization_id=organization_id,
        job_id=job.id,
        job_revision_id=revision.id,
        status="queued",
        priority=str(definition.get("priority", "normal")),
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        scheduled_at=scheduled_at or now,
        payload={
            "requestedBy": str(principal.user_id),
            "retryCount": 0,
            "trigger": trigger
            or {
                "type": "manual",
                "requestedByType": "human",
                "requestedBy": str(principal.user_id),
                "requestedAt": now.isoformat(),
                "key": None,
                "eventId": None,
                "payload": None,
                "scheduledFor": None,
                "dedupeKey": None,
                "sourceJobId": None,
                "sourceWorkItemId": None,
                "configId": None,
            },
        },
    )
    session.add(item)
    await session.flush()
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="work_item.queued",
        category="workforce",
        resource_type="work_item",
        resource_id=str(item.id),
        resource_name=job.name,
        correlation_id=correlation_id,
        outcome="queued",
        summary=f"Job {job.name} queued.",
    )
    await session.commit()
    await session.refresh(item)
    await request_runtime_execution(
        organization_id=organization_id,
        work_item_id=item.id,
        expected_step=0,
        reason="queue",
    )
    return item


async def delete_job_data(
    session: AsyncSession, job: Job
) -> tuple[int, int]:
    items = list(
        (await session.scalars(select(WorkItem).where(WorkItem.job_id == job.id))).all()
    )
    item_ids = [item.id for item in items]
    runs = (
        list(
            (
                await session.scalars(
                    select(Run).where(Run.work_item_id.in_(item_ids))
                )
            ).all()
        )
        if item_ids
        else []
    )
    run_ids = [run.id for run in runs]
    if run_ids:
        action_ids = list(
            (
                await session.scalars(
                    select(Action.id).where(Action.run_id.in_(run_ids))
                )
            ).all()
        )
        if action_ids:
            await session.execute(
                delete(Approval).where(Approval.action_id.in_(action_ids))
            )
            await session.execute(delete(Action).where(Action.id.in_(action_ids)))
        await session.execute(
            delete(RunStep).where(RunStep.run_id.in_(run_ids))
        )
        await session.execute(
            delete(Artifact).where(Artifact.run_id.in_(run_ids))
        )
        await session.execute(delete(Run).where(Run.id.in_(run_ids)))
    results = list(
        (await session.scalars(select(Result).where(Result.job_id == job.id))).all()
    )
    result_ids = [result.id for result in results]
    if result_ids:
        await session.execute(
            delete(ResultExport).where(ResultExport.result_id.in_(result_ids))
        )
        await session.execute(
            delete(ResultVersion).where(ResultVersion.result_id.in_(result_ids))
        )
        await session.execute(delete(Result).where(Result.id.in_(result_ids)))
    if item_ids:
        await session.execute(
            delete(WorkItem).where(WorkItem.id.in_(item_ids))
        )
    await session.execute(
        delete(JobRevision).where(JobRevision.job_id == job.id)
    )
    await session.delete(job)
    return len(items), len(runs)


async def save_trigger_config(
    session: AsyncSession,
    organization_id: UUID,
    job_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> dict[str, Any]:
    require_permission(principal, "jobs.manage")
    job = await session.scalar(
        select(Job).where(Job.organization_id == organization_id, Job.id == job_id)
    )
    if job is None:
        raise not_found("Job")
    current = await current_revision(session, job)
    definition = dict(current.definition)
    now = utcnow()
    raw_existing = definition.get("triggerConfig")
    existing: dict[str, Any] = (
        dict(raw_existing) if isinstance(raw_existing, dict) else {}
    )
    raw_schedule = payload.get("schedule")
    schedule: dict[str, Any] = (
        dict(raw_schedule) if isinstance(raw_schedule, dict) else {}
    )
    next_due = (
        _next_schedule_occurrence(schedule, now).isoformat()
        if bool(schedule.get("enabled"))
        else None
    )
    config = {
        "id": str(existing.get("id") or uuid4()),
        "organizationId": str(organization_id),
        "jobId": str(job.id),
        "workerId": str(job.worker_id),
        "revision": int(existing.get("revision", 0)) + 1,
        "schedule": {
            **schedule,
            "nextDueAt": next_due,
        },
        "apiEnabled": bool(payload.get("apiEnabled", False)),
        "internalEventKeys": list(payload.get("internalEventKeys", [])),
        "dependencyJobIds": list(payload.get("dependencyJobIds", [])),
        "createdBy": str(existing.get("createdBy") or principal.user_id),
        "createdAt": str(existing.get("createdAt") or now.isoformat()),
        "updatedBy": str(principal.user_id),
        "updatedAt": now.isoformat(),
    }
    definition["triggerConfig"] = config
    job.current_revision += 1
    session.add(
        JobRevision(
            job_id=job.id,
            revision=job.current_revision,
            definition=definition,
            created_by=principal.user_id,
            created_at=now,
        )
    )
    job.updated_at = now
    await session.commit()
    return config


def trigger_config_v2(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": config["id"],
            "jobId": config["jobId"],
            "revision": config["revision"],
            "createdAt": config["createdAt"],
            "updatedAt": config["updatedAt"],
        },
        "organizationId": config["organizationId"],
        "assignment": {"workerId": config["workerId"]},
        "schedule": config["schedule"],
        "triggers": {
            "apiEnabled": config["apiEnabled"],
            "internalEventKeys": config["internalEventKeys"],
            "dependencyJobIds": config["dependencyJobIds"],
        },
        "ownership": {
            "createdBy": config["createdBy"],
            "updatedBy": config["updatedBy"],
        },
    }


@v1_router.get("/organizations/{organization_id}/jobs")
async def jobs_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.read")
    jobs = list(
        (
            await session.scalars(
                select(Job)
                .where(Job.organization_id == organization_id)
                .order_by(desc(Job.created_at))
            )
        ).all()
    )
    return {
        "jobs": [await job_public(session, job) for job in jobs],
        "summary": {
            "total": len(jobs),
            "draft": sum(x.status == "draft" for x in jobs),
            "active": sum(x.status == "active" for x in jobs),
            "paused": sum(x.status == "paused" for x in jobs),
            "archived": sum(x.status == "archived" for x in jobs),
        },
        "window": {"limit": 500, "truncated": False},
    }


@v2_router.get("/organizations/{organization_id}/jobs")
async def jobs_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    v1 = await jobs_v1(organization_id, principal, session)
    jobs = list(
        (
            await session.scalars(
                select(Job)
                .where(Job.organization_id == organization_id)
                .order_by(desc(Job.created_at))
            )
        ).all()
    )
    return {
        "data": {
            "items": [await job_v2(session, job) for job in jobs],
            "summary": v1["summary"],
            "window": v1["window"],
        }
    }


@v1_router.post("/organizations/{organization_id}/jobs", status_code=201)
async def create_job_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    job = await create_job(session, organization_id, principal, payload)
    return {"job": await job_public(session, job)}


@v2_router.post("/organizations/{organization_id}/jobs", status_code=201)
async def create_job_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    job = await create_job(session, organization_id, principal, payload)
    return {"data": {"job": await job_v2(session, job)}}


@v1_router.put("/organizations/{organization_id}/jobs/{job_id}")
async def update_job_v1(
    organization_id: UUID,
    job_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    job = await update_job(
        session, organization_id, job_id, principal, payload
    )
    return {"job": await job_public(session, job)}


@v2_router.put("/organizations/{organization_id}/jobs/{job_id}")
async def update_job_v2(
    organization_id: UUID,
    job_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    job = await update_job(
        session, organization_id, job_id, principal, payload
    )
    return {"data": {"job": await job_v2(session, job)}}


@v1_router.post("/organizations/{organization_id}/jobs/{job_id}/status")
async def job_status_v1(
    organization_id: UUID,
    job_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.manage")
    job = await session.scalar(
        select(Job).where(Job.organization_id == organization_id, Job.id == job_id)
    )
    if job is None:
        raise not_found("Job")
    new_status = str(payload.get("status", ""))
    if new_status not in {
        "draft",
        "active",
        "paused",
        "waiting_integration",
        "archived",
    }:
        raise HTTPException(400, "Invalid Job status.")
    job.status = new_status
    await session.commit()
    await session.refresh(job)
    return {"job": await job_public(session, job)}


@v2_router.post("/organizations/{organization_id}/jobs/{job_id}/status")
async def job_status_v2(
    organization_id: UUID,
    job_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    await job_status_v1(
        organization_id, job_id, payload, principal, session
    )
    job = await session.get(Job, job_id)
    assert job is not None
    return {"data": {"job": await job_v2(session, job)}}


@v1_router.delete("/organizations/{organization_id}/jobs/{job_id}")
async def delete_job_v1(
    organization_id: UUID,
    job_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.manage")
    job = await session.scalar(
        select(Job).where(Job.organization_id == organization_id, Job.id == job_id)
    )
    if job is None:
        raise not_found("Job")
    items, runs = await delete_job_data(session, job)
    await session.commit()
    return {
        "complete": True,
        "jobId": str(job_id),
        "workItemsRemoved": items,
        "runsTerminated": runs,
        "nextToken": None,
    }


@v2_router.delete("/organizations/{organization_id}/jobs/{job_id}")
async def delete_job_v2(
    organization_id: UUID,
    job_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await delete_job_v1(
            organization_id, job_id, principal, session
        )
    }


@v1_router.post(
    "/organizations/{organization_id}/jobs/{job_id}/run-now", status_code=201
)
async def run_job_v1(
    organization_id: UUID,
    job_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    job = await session.scalar(
        select(Job).where(Job.organization_id == organization_id, Job.id == job_id)
    )
    if job is None:
        raise not_found("Job")
    item = await queue_job(session, organization_id, job, principal)
    return {"workItem": await work_item_public(session, item)}


@v2_router.post(
    "/organizations/{organization_id}/jobs/{job_id}/run-now", status_code=201
)
async def run_job_v2(
    organization_id: UUID,
    job_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    job = await session.scalar(
        select(Job).where(Job.organization_id == organization_id, Job.id == job_id)
    )
    if job is None:
        raise not_found("Job")
    item = await queue_job(session, organization_id, job, principal)
    return {"data": {"workItem": await work_item_v2(session, item)}}


@v1_router.get("/organizations/{organization_id}/work-items")
async def work_items_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.read")
    items = list(
        (
            await session.scalars(
                select(WorkItem)
                .where(WorkItem.organization_id == organization_id)
                .order_by(desc(WorkItem.created_at))
            )
        ).all()
    )
    return {
        "workItems": [await work_item_public(session, x) for x in items],
        "summary": {
            "total": len(items),
            "queued": sum(x.status == "queued" for x in items),
            "cancelled": sum(x.status == "cancelled" for x in items),
            "completed": sum(x.status == "completed" for x in items),
            "failed": sum(x.status == "failed" for x in items),
        },
        "window": {"limit": 500, "truncated": False},
    }


@v2_router.get("/organizations/{organization_id}/work-items")
async def work_items_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    v1 = await work_items_v1(organization_id, principal, session)
    items = list(
        (
            await session.scalars(
                select(WorkItem)
                .where(WorkItem.organization_id == organization_id)
                .order_by(desc(WorkItem.created_at))
            )
        ).all()
    )
    return {
        "data": {
            "items": [await work_item_v2(session, x) for x in items],
            "summary": v1["summary"],
            "window": v1["window"],
        }
    }


async def cancel_item(
    session: AsyncSession,
    organization_id: UUID,
    item_id: UUID,
    principal: HumanPrincipal,
) -> WorkItem:
    require_permission(principal, "jobs.manage")
    item = await session.scalar(
        select(WorkItem).where(
            WorkItem.organization_id == organization_id,
            WorkItem.id == item_id,
        )
    )
    if item is None:
        raise not_found("Work item")
    payload = dict(item.payload or {})
    item.status = "cancelled"
    now = utcnow().isoformat()
    payload.update(
        {
            "cancelledBy": str(principal.user_id),
            "cancelledAt": now,
            "completedAt": now,
        }
    )
    item.payload = payload
    runs = list(
        (
            await session.scalars(
                select(Run).where(Run.work_item_id == item.id)
            )
        ).all()
    )
    for run in runs:
        if run.status not in {"completed", "failed", "cancelled"}:
            run.status = "cancelled"
    await session.commit()
    await session.refresh(item)
    return item


@v1_router.post("/organizations/{organization_id}/work-items/{work_item_id}/cancel")
async def cancel_item_v1(
    organization_id: UUID,
    work_item_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    item = await cancel_item(
        session, organization_id, work_item_id, principal
    )
    return {"workItem": await work_item_public(session, item)}


@v2_router.post("/organizations/{organization_id}/work-items/{work_item_id}/cancel")
async def cancel_item_v2(
    organization_id: UUID,
    work_item_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    item = await cancel_item(
        session, organization_id, work_item_id, principal
    )
    return {"data": {"workItem": await work_item_v2(session, item)}}


@v1_router.post("/organizations/{organization_id}/work-items/clear-queue")
async def clear_queue_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.manage")
    items = list(
        (
            await session.scalars(
                select(WorkItem).where(
                    WorkItem.organization_id == organization_id,
                    WorkItem.status.in_(
                        [
                            "queued",
                            "claimed",
                            "running",
                            "waiting_approval",
                            "waiting_dependency",
                        ]
                    ),
                )
            )
        ).all()
    )
    terminated = 0
    now = utcnow().isoformat()
    for item in items:
        item.status = "cancelled"
        payload = dict(item.payload or {})
        payload.update(
            {
                "cancelledBy": str(principal.user_id),
                "cancelledAt": now,
                "completedAt": now,
            }
        )
        item.payload = payload
        runs = list(
            (
                await session.scalars(
                    select(Run).where(Run.work_item_id == item.id)
                )
            ).all()
        )
        for run in runs:
            if run.status not in {"completed", "failed", "cancelled"}:
                run.status = "cancelled"
                terminated += 1
    await session.commit()
    return {
        "complete": True,
        "removed": len(items),
        "runsTerminated": terminated,
        "nextToken": None,
    }


@v2_router.post("/organizations/{organization_id}/work-items/clear-queue")
async def clear_queue_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {"data": await clear_queue_v1(organization_id, principal, session)}


@v1_router.put("/organizations/{organization_id}/jobs/{job_id}/triggers")
async def save_trigger_v1(
    organization_id: UUID,
    job_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "config": await save_trigger_config(
            session, organization_id, job_id, principal, payload
        )
    }


@v2_router.put("/organizations/{organization_id}/jobs/{job_id}/triggers")
async def save_trigger_v2(
    organization_id: UUID,
    job_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    config = await save_trigger_config(
        session, organization_id, job_id, principal, payload
    )
    return {"data": {"config": trigger_config_v2(config)}}


async def trigger_workspace(
    session: AsyncSession, organization_id: UUID
) -> dict[str, Any]:
    jobs = list(
        (
            await session.scalars(
                select(Job).where(Job.organization_id == organization_id)
            )
        ).all()
    )
    configs: list[dict[str, Any]] = []
    for job in jobs:
        revision = await current_revision(session, job)
        definition = revision.definition if isinstance(revision.definition, dict) else {}
        config = definition.get("triggerConfig")
        if isinstance(config, dict):
            configs.append(config)
    items = list(
        (
            await session.scalars(
                select(WorkItem)
                .where(WorkItem.organization_id == organization_id)
                .order_by(desc(WorkItem.created_at))
                .limit(200)
            )
        ).all()
    )
    history: list[dict[str, Any]] = []
    for item in items:
        payload: dict[str, Any] = (
            dict(item.payload) if isinstance(item.payload, dict) else {}
        )
        raw_trigger = payload.get("trigger")
        trigger: dict[str, Any] = (
            dict(raw_trigger) if isinstance(raw_trigger, dict) else {}
        )
        job = await session.get(Job, item.job_id)
        history.append(
            {
                "id": str(item.id),
                "organizationId": str(organization_id),
                "jobId": str(item.job_id),
                "workerId": str(job.worker_id) if job else "",
                "triggerType": trigger.get("type", "manual"),
                "outcome": (
                    "queued"
                    if item.status not in {"cancelled", "failed"}
                    else "blocked"
                ),
                "key": trigger.get("key"),
                "eventId": trigger.get("eventId"),
                "scheduledFor": trigger.get("scheduledFor"),
                "workItemId": str(item.id),
                "correlationId": item.correlation_id,
                "reason": payload.get("lastError"),
                "actorType": trigger.get("requestedByType", "human"),
                "actorId": trigger.get("requestedBy", "system"),
                "occurredAt": item.created_at.isoformat(),
            }
        )
    return {
        "configs": configs,
        "history": history,
        "summary": {
            "configs": len(configs),
            "scheduled": sum(
                bool(c.get("schedule", {}).get("enabled")) for c in configs
            ),
            "apiEnabled": sum(bool(c.get("apiEnabled")) for c in configs),
            "eventDriven": sum(
                bool(c.get("internalEventKeys")) for c in configs
            ),
            "dependencyDriven": sum(
                bool(c.get("dependencyJobIds")) for c in configs
            ),
            "history": {
                "total": len(history),
                "queued": sum(h["outcome"] == "queued" for h in history),
                "skipped": sum(h["outcome"] == "skipped" for h in history),
                "blocked": sum(h["outcome"] == "blocked" for h in history),
            },
        },
        "window": {
            "configsTruncated": False,
            "historyTruncated": False,
        },
        "scheduler": {"cadenceMinutes": 1, "mode": "queue_only"},
    }


@v1_router.get("/organizations/{organization_id}/triggers")
async def triggers_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.read")
    return await trigger_workspace(session, organization_id)


@v2_router.get("/organizations/{organization_id}/triggers")
async def triggers_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.read")
    result = await trigger_workspace(session, organization_id)
    return {
        "data": {
            "configs": [trigger_config_v2(x) for x in result["configs"]],
            "history": result["history"],
            "summary": result["summary"],
            "window": result["window"],
            "scheduler": result["scheduler"],
        }
    }


async def emit_event(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> dict[str, Any]:
    require_permission(principal, "jobs.run")
    key = str(payload.get("key", "")).strip()
    if not key:
        raise HTTPException(400, "Event key is required.")
    jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.organization_id == organization_id,
                    Job.status == "active",
                )
            )
        ).all()
    )
    queued: list[str] = []
    event_id = str(uuid4())
    for job in jobs:
        revision = await current_revision(session, job)
        definition = (
            revision.definition if isinstance(revision.definition, dict) else {}
        )
        config = (
            definition.get("triggerConfig")
            if isinstance(definition.get("triggerConfig"), dict)
            else None
        )
        if not config or key not in config.get("internalEventKeys", []):
            continue
        trigger = {
            "type": "internal_event",
            "requestedByType": "human",
            "requestedBy": str(principal.user_id),
            "requestedAt": utcnow().isoformat(),
            "key": key,
            "eventId": event_id,
            "payload": payload.get("payload"),
            "scheduledFor": None,
            "dedupeKey": f"event:{event_id}:{job.id}",
            "sourceJobId": payload.get("sourceJobId"),
            "sourceWorkItemId": payload.get("sourceWorkItemId"),
            "configId": config.get("id"),
        }
        item = await queue_job(
            session, organization_id, job, principal, trigger=trigger
        )
        queued.append(str(item.id))
    return {
        "eventId": event_id,
        "key": key,
        "queued": len(queued),
        "workItemIds": queued,
    }


@v1_router.post("/organizations/{organization_id}/triggers/events", status_code=202)
async def emit_event_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await emit_event(session, organization_id, principal, payload)


@v2_router.post("/organizations/{organization_id}/triggers/events", status_code=202)
async def emit_event_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await emit_event(session, organization_id, principal, payload)
    }


async def _provision_managed_job_authority(
    session: AsyncSession,
    organization_id: UUID,
    worker: Worker,
    principal: HumanPrincipal,
    scopes: list[str],
) -> None:
    profile = worker.profile if isinstance(worker.profile, dict) else {}
    if profile.get("agentIdentityProvisioning") != "automatic":
        return

    requested = {scope.strip() for scope in scopes if scope.strip()}
    if not requested:
        return

    existing_rows = list(
        (
            await session.scalars(
                select(CapabilityProfile).where(
                    CapabilityProfile.organization_id == organization_id,
                    CapabilityProfile.agent_id == worker.agent_identity_id,
                )
            )
        ).all()
    )
    by_scope = {row.scope: row for row in existing_rows}
    now = utcnow()
    for scope in sorted(requested):
        existing = by_scope.get(scope)
        if existing is not None:
            existing.active = True
            existing.updated_at = now
            continue
        session.add(
            CapabilityProfile(
                organization_id=organization_id,
                agent_id=worker.agent_identity_id,
                scope=scope,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )

    registry = execution_provider_registry()
    async def upsert_policy(
        *,
        effect: str,
        selected_scopes: set[str],
        priority: int,
    ) -> None:
        name = f"Managed Runtime {effect} · {worker.id}"
        policy = await session.scalar(
            select(Policy).where(
                Policy.organization_id == organization_id,
                Policy.name == name,
            )
        )
        if policy is None:
            policy = Policy(
                organization_id=organization_id,
                name=name,
                status="enabled",
                current_revision=1,
                created_at=now,
                updated_at=now,
            )
            session.add(policy)
            await session.flush()
            revision_number = 1
            created_at = now.isoformat()
        else:
            policy.status = "enabled"
            policy.current_revision += 1
            policy.updated_at = now
            revision_number = policy.current_revision
            created_at = policy.created_at.isoformat()

        session.add(
            PolicyRevision(
                policy_id=policy.id,
                revision=revision_number,
                effect=effect,
                priority=priority,
                selectors={
                    "agentIds": [str(worker.agent_identity_id)],
                    "resourceIds": [],
                    "actions": [],
                    "scopes": sorted(selected_scopes),
                    "risks": [],
                    "_meta": {
                        "description": "Automatic least-authority policy for managed Worker Jobs.",
                        "createdBy": str(principal.user_id),
                        "updatedBy": str(principal.user_id),
                        "createdAt": created_at,
                    },
                },
                created_at=now,
            )
        )

    all_active_scopes = {row.scope for row in existing_rows if row.active} | requested
    allow_all: set[str] = set()
    approval_all: set[str] = set()
    for scope in all_active_scopes:
        providers = registry.capability_providers(scope)
        capability = next(
            (
                capability
                for provider in providers
                for capability in provider.manifest.capabilities
                if capability.scope == scope
            ),
            None,
        )
        if capability is not None and (
            capability.approval_recommendation == "none"
            and capability.risk not in {"high", "critical"}
        ):
            allow_all.add(scope)
        else:
            approval_all.add(scope)

    if allow_all:
        await upsert_policy(effect="allow", selected_scopes=allow_all, priority=100)
    if approval_all:
        await upsert_policy(
            effect="require_approval",
            selected_scopes=approval_all,
            priority=200,
        )

    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="managed_authority.provisioned",
        category="security",
        resource_type="worker",
        resource_id=str(worker.id),
        resource_name=worker.name,
        outcome="updated",
        summary=f"Managed Runtime authority provisioned for {worker.name}.",
        metadata={
            "scopes": sorted(all_active_scopes),
            "automatic": True,
        },
    )
    await session.commit()


async def _apply_quick_timing(
    session: AsyncSession,
    organization_id: UUID,
    job: Job,
    principal: HumanPrincipal,
    timing: dict[str, Any],
) -> None:
    mode = str(timing.get("mode") or "manual")
    job.status = "active"
    job.updated_at = utcnow()
    await session.commit()

    if mode in {"interval", "daily", "weekly", "custom", "event", "dependency"}:
        await save_trigger_config(
            session,
            organization_id,
            job.id,
            principal,
            _quick_trigger_payload(timing),
        )

    if mode in {"start_now", "interval", "daily", "weekly", "custom"}:
        await queue_job(session, organization_id, job, principal)
        return

    if mode == "once":
        scheduled_for = _parse_timestamp(timing.get("at"))
        if scheduled_for is None:
            raise HTTPException(400, "One-time scheduling requires a valid timestamp.")
        trigger = {
            "type": "schedule",
            "requestedByType": "human",
            "requestedBy": str(principal.user_id),
            "requestedAt": utcnow().isoformat(),
            "key": None,
            "eventId": None,
            "payload": None,
            "scheduledFor": scheduled_for.isoformat(),
            "dedupeKey": f"once:{job.id}:{scheduled_for.isoformat()}",
            "sourceJobId": None,
            "sourceWorkItemId": None,
            "configId": None,
        }
        await queue_job(
            session,
            organization_id,
            job,
            principal,
            trigger=trigger,
            scheduled_at=scheduled_for,
        )


async def worker_quick_start(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> dict[str, Any]:
    raw_worker_input = payload.get("worker")
    worker_input: dict[str, Any] = (
        dict(raw_worker_input) if isinstance(raw_worker_input, dict) else {}
    )
    identity_mode = worker_input.get("identityMode", "existing")
    credential = None
    created_auto = False
    if identity_mode == "automatic":
        agent, credential = await automatic_agent(
            session,
            organization_id,
            principal,
            str(worker_input.get("name", "Worker")),
        )
        worker_input = {
            **worker_input,
            "agentIdentityId": str(agent.id),
        }
        created_auto = True
    worker = await create_worker(
        session,
        organization_id,
        principal,
        worker_input,
        provisioning="automatic" if created_auto else "existing",
    )
    worker.status = "active"
    worker.updated_at = utcnow()
    await session.commit()
    job_ids: list[str] = []
    for entry in payload.get("jobs", []):
        if not isinstance(entry, dict):
            continue
        job_entry: dict[str, Any] = dict(entry)
        job = await create_job(
            session,
            organization_id,
            principal,
            {**job_entry, "workerId": str(worker.id)},
        )
        await _provision_managed_job_authority(
            session,
            organization_id,
            worker,
            principal,
            [str(scope) for scope in job_entry.get("requiredCapabilities", [])],
        )
        raw_timing = job_entry.get("timing")
        timing: dict[str, Any] = (
            dict(raw_timing) if isinstance(raw_timing, dict) else {"mode": "manual"}
        )
        await _apply_quick_timing(
            session,
            organization_id,
            job,
            principal,
            timing,
        )
        job_ids.append(str(job.id))
    return {
        "workerId": str(worker.id),
        "jobIds": job_ids,
        "identity": {
            "agentId": str(worker.agent_identity_id),
            "createdAutomatically": created_auto,
            "credential": credential,
        },
        "readiness": {
            "state": "started",
            "blockers": [],
            "workerId": str(worker.id),
            "jobIds": job_ids,
        },
    }


@v1_router.post(
    "/organizations/{organization_id}/workforce/quick-start", status_code=201
)
async def worker_quick_start_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await worker_quick_start(
        session, organization_id, principal, payload
    )


@v2_router.post(
    "/organizations/{organization_id}/workforce/quick-start", status_code=201
)
async def worker_quick_start_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await worker_quick_start(
            session, organization_id, principal, payload
        )
    }


async def job_quick_start(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> dict[str, Any]:
    raw_job_payload = payload.get("job")
    job_payload: dict[str, Any] = (
        dict(raw_job_payload) if isinstance(raw_job_payload, dict) else {}
    )
    job = await create_job(session, organization_id, principal, job_payload)
    worker = await session.get(Worker, job.worker_id)
    if worker is None:
        raise HTTPException(500, "Job Worker is unavailable.")
    await _provision_managed_job_authority(
        session,
        organization_id,
        worker,
        principal,
        [str(scope) for scope in job_payload.get("requiredCapabilities", [])],
    )
    raw_timing = payload.get("timing")
    timing: dict[str, Any] = (
        dict(raw_timing) if isinstance(raw_timing, dict) else {"mode": "manual"}
    )
    await _apply_quick_timing(
        session,
        organization_id,
        job,
        principal,
        timing,
    )
    return {
        "jobId": str(job.id),
        "workerId": str(job.worker_id),
        "readiness": {
            "state": "started",
            "blockers": [],
            "workerId": str(job.worker_id),
            "jobIds": [str(job.id)],
        },
    }


@v1_router.post(
    "/organizations/{organization_id}/jobs/quick-start", status_code=201
)
async def job_quick_start_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await job_quick_start(
        session, organization_id, principal, payload
    )


@v2_router.post(
    "/organizations/{organization_id}/jobs/quick-start", status_code=201
)
async def job_quick_start_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await job_quick_start(
            session, organization_id, principal, payload
        )
    }
