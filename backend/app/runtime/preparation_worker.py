"""Prompt HTTP acknowledgment with PostgreSQL recovery of accepted preparation commands."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select

from app.bootstrap.settings import settings
from app.infrastructure.database.models import (
    ConversationCommand,
    Job,
    JobRevision,
    PlannerDecision,
)
from app.infrastructure.database.session import session_factory

logger = logging.getLogger("uvicorn.error")
_tasks = {}
_decision_ids = {}


def start_preparation(command_id, decision_id=None):
    previous = _tasks.get(command_id)
    if previous and decision_id and _decision_ids.get(command_id) != decision_id:
        previous.cancel()
        _tasks.pop(command_id, None)
    if command_id not in _tasks:
        task = asyncio.create_task(_prepare(command_id))
        _tasks[command_id] = task
        _decision_ids[command_id] = decision_id
        def finished(completed):
            if _tasks.get(command_id) is completed:
                _tasks.pop(command_id, None)
                _decision_ids.pop(command_id, None)
        task.add_done_callback(finished)


def start_worker_preparation(job_id):
    key = ("job", job_id)
    if key not in _tasks:
        task = asyncio.create_task(_prepare_worker(job_id))
        _tasks[key] = task
        task.add_done_callback(lambda completed: _tasks.pop(key, None))


async def _prepare_worker(job_id):
    from app.application.services.integration_worker_readiness import reconcile_worker_job
    from app.infrastructure.ai.provider import ai_gateway_from_settings

    try:
        async with session_factory() as session:
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
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
    from app.application.services.integration_tasks import prepare_integration_task

    try:
        logger.info("AI preparation background started command=%s", command_id)
        async with session_factory() as session:
            from app.application.services.integration_tasks import report_preparation_wait
            command = await session.scalar(select(ConversationCommand).where(
                ConversationCommand.id == command_id).with_for_update())
            if command and not command.target_id and command.status == "accepted":
                await report_preparation_wait(session, command, status="accepted",
                    category="preparation_started", message="I’m preparing your saved task now. I’ll report any blocker here.")
                await session.commit()
            await prepare_integration_task(session, command_id)
            await session.commit()
            logger.info("AI preparation background ended command=%s status=%s", command_id,
                command.status if command else "not_found")
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
            from app.application.services.integration_tasks import report_preparation_wait

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
    for command_id in rows:
        start_preparation(command_id)
    for job_id in jobs:
        start_worker_preparation(job_id)


async def preparation_recovery_loop():
    while True:
        try:
            await recover_preparations()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AI preparation recovery unavailable; pending commands remain saved")
        await asyncio.sleep(15)


async def stop_preparations():
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
