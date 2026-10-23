"""Prompt HTTP acknowledgment with PostgreSQL recovery of accepted preparation commands."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy import func, or_, select

from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.infrastructure.database.models import (
    ConversationCommand,
    Job,
    JobRevision,
    PlannerDecision,
)
from audoryn_shared.infrastructure.database.session import session_factory
from audoryn_shared.infrastructure.database.usage_records import record_usage
from audoryn_shared.infrastructure.redis.coordination import RedisCoordinator, keep_leases
from audoryn_shared.observability.usage import Usage, label_usage, metered

logger = logging.getLogger("uvicorn.error")
_tasks = {}
_decision_ids = {}

# Recovery runs often while preparations are active and rarely when nothing is pending, so an
# idle backend lets the database scale to zero (Neon suspends a compute after five minutes
# without queries). A finished preparation wakes it, since it may have scheduled a retry.
RECOVERY_BUSY_SECONDS = 15.0
# A copy that stops without releasing its lease frees the preparation within this time.
LEASE_SECONDS = 90
_recovery_wake: asyncio.Event | None = None


def _wake_recovery(completed=None):
    # A preparation another copy holds ends at once; waking recovery for it would spin.
    held_elsewhere = (completed is not None and not completed.cancelled()
                      and completed.exception() is None and completed.result() is False)
    if _recovery_wake is not None and not held_elsewhere:
        _recovery_wake.set()


def _coordinator():
    return RedisCoordinator.from_settings()


def next_recovery_delay(*, started: int, active: int, next_retry_at, now, idle_seconds: float) -> float:
    """Seconds until the next recovery pass."""
    if started or active:
        return RECOVERY_BUSY_SECONDS
    if next_retry_at is not None:
        until_retry = (next_retry_at - now).total_seconds() + 1
        return max(RECOVERY_BUSY_SECONDS, min(idle_seconds, until_retry))
    return max(RECOVERY_BUSY_SECONDS, idle_seconds)


def start_preparation(command_id, decision_id=None):
    previous = _tasks.get(command_id)
    if previous and decision_id and _decision_ids.get(command_id) != decision_id:
        previous.cancel()
        _tasks.pop(command_id, None)
    if command_id not in _tasks:
        # A replaced task holds the lease until its cancellation finishes.
        task = asyncio.create_task(_metered(_prepare, command_id, kind="preparation.task", after=previous))
        _tasks[command_id] = task
        _decision_ids[command_id] = decision_id
        def finished(completed):
            if _tasks.get(command_id) is completed:
                _tasks.pop(command_id, None)
                _decision_ids.pop(command_id, None)
        task.add_done_callback(finished)
        task.add_done_callback(_wake_recovery)


def start_worker_preparation(job_id):
    key = ("job", job_id)
    if key not in _tasks:
        task = asyncio.create_task(_metered(_prepare_worker, job_id, kind="preparation.worker"))
        _tasks[key] = task
        task.add_done_callback(lambda completed: _tasks.pop(key, None))
        task.add_done_callback(_wake_recovery)


async def _metered(prepare, target_id, *, kind, after=None):
    """Run one preparation with its own usage meter, recorded against its organisation.

    Every copy of the backend recovers the same pending preparations, so each runs under a lease
    renewed while it runs; returns False at once when another copy holds it (that copy finishes
    it, or its lease lapses and the next recovery pass takes over)."""
    if after is not None:
        await asyncio.gather(after, return_exceptions=True)
    coordinator = _coordinator()
    try:
        lease = await coordinator.acquire_lock(f"preparation-run:{kind}:{target_id}", ttl_seconds=LEASE_SECONDS)
    except Exception as error:  # noqa: BLE001 - the work stays saved; the next recovery pass retries
        logger.warning("Preparation lease unavailable %s=%s error=%s", kind, target_id, type(error).__name__)
        lease = None
    if lease is None:
        with suppress(Exception):
            await coordinator.close()
        return False
    heartbeat = asyncio.create_task(keep_leases(coordinator, (lease,), asyncio.current_task()))
    usage = Usage()
    try:
        with metered(usage):
            return await prepare(target_id)
    finally:
        heartbeat.cancel()
        with suppress(Exception):
            await coordinator.release_lock(lease)
        with suppress(Exception):
            await coordinator.close()
        with suppress(Exception):
            await record_usage(usage, kind=kind, organization_id=usage.labels.get("organization_id"),
                outcome=usage.labels.get("outcome"))


async def _prepare_worker(job_id):
    from audoryn_shared.application.services.integration_worker_readiness import (
        reconcile_worker_job,
    )
    from audoryn_shared.infrastructure.ai.provider import ai_gateway_from_settings

    try:
        async with session_factory() as session:
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job:
                label_usage(organization_id=job.organization_id)
            if job and job.status == "waiting_integration":
                revision = await session.scalar(
                    select(JobRevision).where(
                        JobRevision.job_id == job.id, JobRevision.revision == job.current_revision
                    )
                )
                if revision and (revision.definition.get("autonomy") or {}).get(
                    "integrationFoundation"
                ):
                    await reconcile_worker_job(
                        session,
                        ai_gateway_from_settings(
                            session=session,
                            workload="complex",
                            purpose="integration_worker_preparation",
                        ),
                        job,
                        revision,
                    )
            await session.commit()
    except asyncio.CancelledError:
        logger.warning("AI worker preparation interrupted job=%s; checkpoint retained", job_id)
        raise
    except Exception as error:
        logger.exception(
            "AI worker preparation failed job=%s error_type=%s", job_id, type(error).__name__
        )


async def _prepare(command_id):
    from audoryn_execution.application.services.integration_tasks import prepare_integration_task

    try:
        logger.info("AI preparation background started command=%s", command_id)
        async with session_factory() as session:
            from audoryn_execution.application.services.integration_tasks import (
                report_preparation_wait,
            )
            command = await session.scalar(select(ConversationCommand).where(
                ConversationCommand.id == command_id).with_for_update())
            if command:
                label_usage(organization_id=command.organization_id)
            if command and not command.target_id and command.status == "accepted":
                await report_preparation_wait(session, command, status="accepted",
                    category="preparation_started", message="I’m preparing your saved task now. I’ll report any blocker here.")
                await session.commit()
            await prepare_integration_task(session, command_id)
            await session.commit()
            logger.info("AI preparation background ended command=%s status=%s", command_id,
                command.status if command else "not_found")
            label_usage(outcome=command.status if command else "not_found")
    except asyncio.CancelledError:
        logger.warning(
            "AI preparation interrupted command=%s; saved checkpoint retained", command_id
        )
        raise
    except Exception as error:
        logger.exception(
            "AI preparation failed command=%s error_type=%s", command_id, type(error).__name__
        )
        # Report using a fresh transaction even if preparation/database validation failed.
        try:
            from audoryn_execution.application.services.integration_tasks import (
                report_preparation_wait,
            )

            async with session_factory() as session:
                command = await session.get(ConversationCommand, command_id)
                if (
                    command
                    and not command.target_id
                    and command.status in {"accepted", "waiting_ai"}
                ):
                    await report_preparation_wait(
                        session,
                        command,
                        status="waiting_ai",
                        category="preparation_failure",
                        message="Task preparation encountered a system problem. Your request is saved; no new provider work was started.",
                    )
                    await session.commit()
        except Exception:
            logger.exception("AI preparation reporting failed command=%s", command_id)


async def recover_preparations():
    """Restarts preparations that should be running. Returns how many it started and the
    earliest scheduled retry still in the future (or None)."""
    now = datetime.now(UTC)
    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(ConversationCommand.id)
                .outerjoin(
                    PlannerDecision,
                    (PlannerDecision.command_id == ConversationCommand.id)
                    & (PlannerDecision.organization_id == ConversationCommand.organization_id)
                    & (PlannerDecision.purpose == "integration_preparation"),
                )
                .where(
                    ConversationCommand.family == "integration.execute",
                    ConversationCommand.target_id.is_(None),
                    ConversationCommand.status.in_(["accepted", "waiting_ai"]),
                    or_(
                        PlannerDecision.id.is_(None) & (ConversationCommand.status == "accepted"),
                        (PlannerDecision.status.in_(["pending", "accepted"]))
                        & or_(PlannerDecision.retry_at.is_(None), PlannerDecision.retry_at <= now),
                    ),
                )
                .limit(settings.runtime_sweep_limit)
            )
        ).all()
        jobs = (
            await session.scalars(
                select(Job.id)
                .join(
                    JobRevision,
                    (JobRevision.job_id == Job.id) & (JobRevision.revision == Job.current_revision),
                )
                .join(
                    PlannerDecision,
                    (PlannerDecision.preparation_revision_id == JobRevision.id)
                    & (PlannerDecision.organization_id == Job.organization_id),
                )
                .where(
                    Job.status == "waiting_integration",
                    PlannerDecision.status.in_(["pending", "accepted"]),
                    or_(PlannerDecision.retry_at.is_(None), PlannerDecision.retry_at <= now),
                )
                .limit(settings.runtime_sweep_limit)
            )
        ).all()
        next_retry_at = None
        if not rows and not jobs:
            next_retry_at = await session.scalar(
                select(func.min(PlannerDecision.retry_at)).where(
                    PlannerDecision.status.in_(["pending", "accepted"]),
                    PlannerDecision.retry_at > now,
                )
            )
    for command_id in rows:
        start_preparation(command_id)
    for job_id in jobs:
        start_worker_preparation(job_id)
    return len(rows) + len(jobs), next_retry_at


async def preparation_recovery_loop():
    global _recovery_wake
    _recovery_wake = asyncio.Event()
    idle = float(settings.preparation_recovery_idle_seconds)
    while True:
        delay = RECOVERY_BUSY_SECONDS
        try:
            started, next_retry_at = await recover_preparations()
            delay = next_recovery_delay(started=started, active=len(_tasks), next_retry_at=next_retry_at, now=datetime.now(UTC), idle_seconds=idle)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AI preparation recovery unavailable; pending commands remain saved")
        _recovery_wake.clear()
        try:
            await asyncio.wait_for(_recovery_wake.wait(), timeout=delay)
        except TimeoutError:
            pass


async def stop_preparations():
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
