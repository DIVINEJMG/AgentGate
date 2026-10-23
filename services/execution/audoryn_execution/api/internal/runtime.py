import asyncio
import base64
import binascii
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import select

from audoryn_execution.application.messages import planning_paused
from audoryn_execution.application.services.live_results import notify_task_conversation
from audoryn_execution.infrastructure.database.dispatch import WorkItemDispatchRepository
from audoryn_execution.runtime.admission import admit_due_item
from audoryn_execution.runtime.managed import ManagedRuntimeExecutor, RuntimeStepOutcome
from audoryn_shared.api.jobs_routes import current_revision, queue_due_schedules, queue_job
from audoryn_shared.application import internal_calls
from audoryn_shared.application.console_reads.signing import (
    ReplayStoreUnavailable,
    ServiceRequestRejected,
)
from audoryn_shared.application.services import entitlements
from audoryn_shared.application.services.browser_origin_authority import (
    reconcile_ai_job_browser_origins,
)
from audoryn_shared.application.services.cutover import CutoverController
from audoryn_shared.application.services.integration_foundation import notify_integration_work
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.domain.ai.providers import AIProviderError
from audoryn_shared.domain.identity.principals import HumanPrincipal
from audoryn_shared.domain.jobs.dispatch import ScheduledDispatch
from audoryn_shared.execution.bootstrap import browser_provider
from audoryn_shared.infrastructure.database.models import (
    Approval,
    Job,
    QueueDeliveryFailure,
    Run,
    RunStep,
    WorkItem,
)
from audoryn_shared.infrastructure.database.outbox import TransactionalOutbox
from audoryn_shared.infrastructure.database.session import session_factory
from audoryn_shared.infrastructure.database.usage_records import metered_endpoint, record_usage
from audoryn_shared.infrastructure.qstash.verifier import QStashSignatureVerifier
from audoryn_shared.infrastructure.redis.coordination import Lease, RedisCoordinator, keep_leases
from audoryn_shared.infrastructure.storage.provider import object_storage_from_settings
from audoryn_shared.observability.frames import app_frames
from audoryn_shared.observability.usage import Usage, count_plan_usage, metered
from audoryn_shared.runtime.qstash_trigger import request_runtime_execution

router = APIRouter(prefix="/internal/v1/runtime", tags=["internal-runtime"])
logger = logging.getLogger(__name__)
_runtime_tasks: set[asyncio.Task] = set()


def _verify_qstash(request: Request, raw: bytes, signature: str | None) -> None:
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QStash signature required.",
        )
    try:
        QStashSignatureVerifier().verify(
            body=raw.decode("utf-8"),
            signature=signature,
            url=str(request.url),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash signature.",
        ) from exc


async def _verify_caller(request: Request, raw: bytes, signature: str | None) -> None:
    """A QStash delivery, or a due step sent straight from AgentGate's api process (Phase 3)."""
    if signature or not internal_calls.is_signed(request.headers.items()):
        _verify_qstash(request, raw, signature)
        return
    try:
        await internal_calls.verify(scope=internal_calls.EXECUTE_SCOPE, method=request.method,
                                    target=request.url.path, body=raw, headers=request.headers.items())
    except ReplayStoreUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Internal signature check unavailable.") from exc
    except ServiceRequestRejected as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid internal signature.") from exc


def admission_wait_seconds(waits: int) -> int:
    """How long a due item waits before asking again while other work is ahead of it: 5 s at
    first, doubling every second wait up to 30 s. An item ahead is normally admitted within
    seconds; the longer waits only apply when its delivery was lost, until the sweep recovers it."""
    return min(30, 5 * 2 ** (max(0, waits) // 2))


def _runtime_step(item: WorkItem) -> int:
    payload = item.payload if isinstance(item.payload, dict) else {}
    raw = payload.get("runtime")
    runtime = raw if isinstance(raw, dict) else {}
    return max(0, int(runtime.get("currentStep", 0)))


async def _recover_runtime_item(session, message: "ExecutePayload") -> WorkItem:
    # Cancellation may invalidate a connection and expires ORM attributes.
    # Re-read by immutable delivery IDs after rollback, never use the stale item.
    await session.rollback()
    item = await session.scalar(select(WorkItem).where(
        WorkItem.organization_id == message.organization_id, WorkItem.id == message.work_item_id))
    if item is None:
        raise HTTPException(404, "Work item not found during recovery.")
    runtime = dict((item.payload or {}).get("runtime") or {})
    phase_started = runtime.get("plannerPhaseStartedAt")
    if phase_started and runtime.get("plannerPhase") in {"preparing_tools", "awaiting_ai"}:
        elapsed = max(0, (datetime.now(UTC) - datetime.fromisoformat(phase_started)).total_seconds())
        timing = dict(runtime.get("plannerTiming") or {})
        key = "aiResponseSeconds" if runtime.get("plannerPhase") == "awaiting_ai" else "preparationSeconds"
        timing[key] = round(elapsed, 3)
        runtime["plannerTiming"] = timing
        item.payload = {**item.payload, "runtime": runtime}
    return item


def _runtime_failure_category(message: str) -> str:
    lowered = message.lower()
    if "policy" in lowered or "denied" in lowered:
        return "policy_denial"
    if any(token in lowered for token in ("browser", "navigation", "page", "site")):
        return "browser_site_failure"
    if "integration" in lowered:
        return "missing_integration"
    if "approval" in lowered:
        return "approval_wait"
    if any(token in lowered for token in ("authentication", "credential", "unauthorized")):
        return "authentication_expiry"
    if any(
        token in lowered
        for token in (
            "database",
            "storage",
            "runtime job definition is unavailable",
            "runtime worker is unavailable",
            "runtime agent identity is unavailable",
        )
    ):
        return "internal_platform_failure"
    return "task_failure"


class ExecutePayload(BaseModel):
    organization_id: UUID
    work_item_id: UUID
    expected_step: int = Field(default=0, ge=0)
    reason: str = Field(default="qstash", max_length=80)


class ScheduleDuePayload(BaseModel):
    organization_id: UUID
    job_id: UUID
    due: datetime


# QStash delivers delayed messages to within a few seconds; a slightly early wake-up waits.
SCHEDULE_EARLY_WAIT_SECONDS = 30


class BrowserOriginReconcilePayload(BaseModel):
    organization_id: UUID
    job_id: UUID
    run_after_reconcile: bool = False


class DispatchPayload(BaseModel):
    organization_id: UUID
    job_id: UUID
    job_revision_id: UUID
    scheduled_at: datetime
    correlation_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=180)
    payload: dict[str, object] = Field(default_factory=dict)

    def to_dispatch(self) -> ScheduledDispatch:
        return ScheduledDispatch(
            organization_id=self.organization_id,
            job_id=self.job_id,
            job_revision_id=self.job_revision_id,
            idempotency_key=self.idempotency_key,
            correlation_id=self.correlation_id,
            scheduled_at=self.scheduled_at,
            payload=self.payload,
        )


@router.post("/heartbeat")
@metered_endpoint("runtime.heartbeat")
async def heartbeat(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    if not upstash_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QStash signature required.",
        )

    raw = await request.body()
    try:
        QStashSignatureVerifier().verify(
            body=raw.decode("utf-8"),
            signature=upstash_signature,
            url=str(request.url),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash signature.",
        ) from exc

    return {"status": "ok", "source": "qstash"}


class FailureCallbackPayload(BaseModel):
    status: int | None = None
    retried: int = 0
    maxRetries: int = 0
    dlqId: str | None = None
    sourceMessageId: str
    url: str
    sourceBody: str | None = None


@router.post("/failures")
async def failure_callback(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, str]:
    if not upstash_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QStash signature required.",
        )

    raw = await request.body()
    try:
        QStashSignatureVerifier().verify(
            body=raw.decode("utf-8"),
            signature=upstash_signature,
            url=str(request.url),
        )
        failure = FailureCallbackPayload.model_validate(json.loads(raw))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash failure callback.",
        ) from exc

    organization_id: UUID | None = None
    if failure.sourceBody:
        try:
            decoded = base64.b64decode(failure.sourceBody).decode("utf-8")
            source_payload = json.loads(decoded)
            organization_id = UUID(str(source_payload["organization_id"]))
        except (
            binascii.Error,
            json.JSONDecodeError,
            KeyError,
            UnicodeDecodeError,
            ValueError,
        ):
            logger.warning(
                "QStash failure callback has no tenant lineage: %s",
                failure.sourceMessageId,
            )

    if organization_id is None:
        return {"status": "logged", "sourceMessageId": failure.sourceMessageId}

    async with session_factory() as session:
        existing = await session.scalar(
            select(QueueDeliveryFailure).where(
                QueueDeliveryFailure.source_message_id == failure.sourceMessageId
            )
        )
        if existing is None:
            session.add(
                QueueDeliveryFailure(
                    organization_id=organization_id,
                    source_message_id=failure.sourceMessageId,
                    dlq_id=failure.dlqId,
                    status_code=failure.status,
                    retried=failure.retried,
                    max_retries=failure.maxRetries,
                    destination_url=failure.url,
                    failure_payload=failure.model_dump(),
                )
            )
            await session.commit()

    return {"status": "recorded", "sourceMessageId": failure.sourceMessageId}


@router.post("/storage-smoke")
async def storage_smoke(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    if not upstash_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QStash signature required.",
        )

    raw = await request.body()
    try:
        QStashSignatureVerifier().verify(
            body=raw.decode("utf-8"),
            signature=upstash_signature,
            url=str(request.url),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash signature.",
        ) from exc

    storage = object_storage_from_settings()
    key = f"system/smoke/{uuid4()}.txt"
    content = b"audoryn-upstash-blob-smoke"
    reference = await storage.put(
        key=key,
        content=content,
        media_type="text/plain",
    )
    try:
        downloaded = await storage.get(key=key)
        signed_url = await storage.signed_url(key=key, expires_seconds=60)
        if downloaded != content:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Blob round-trip content mismatch.",
            )
        return {
            "status": "ok",
            "uploaded": reference.size_bytes == len(content),
            "downloaded": True,
            "signedUrl": signed_url.startswith("https://"),
            "deleted": True,
        }
    finally:
        await storage.delete(key=key)


@router.post("/reconcile-browser-origin")
async def reconcile_browser_origin(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    raw = await request.body()
    _verify_qstash(request, raw, upstash_signature)
    try:
        message = BrowserOriginReconcilePayload.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid Browser-origin reconciliation payload.",
        ) from exc

    async with session_factory() as session:
        job = await session.scalar(
            select(Job).where(
                Job.organization_id == message.organization_id,
                Job.id == message.job_id,
            )
        )
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found.",
            )
        revision = await current_revision(session, job)
        principal = HumanPrincipal(
            user_id=revision.created_by,
            organization_id=message.organization_id,
            membership_id=UUID(int=0),
            role="system",
            permissions=frozenset(
                {
                    "integrations.manage",
                    "jobs.manage",
                    "jobs.run",
                }
            ),
        )
        try:
            job, origins, created = await reconcile_ai_job_browser_origins(
                session,
                organization_id=message.organization_id,
                job_id=message.job_id,
                principal=principal,
            )
        except (LookupError, PermissionError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

        work_item_id: str | None = None
        if message.run_after_reconcile:
            item = await queue_job(
                session,
                message.organization_id,
                job,
                principal,
            )
            work_item_id = str(item.id)

        current = await current_revision(session, job)
        return {
            "status": "ok",
            "jobId": str(job.id),
            "jobRevision": current.revision,
            "authorizedOrigins": list(origins),
            "managedResourcesCreated": created,
            "workItemId": work_item_id,
        }


@router.post("/dispatch")
async def dispatch(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    if not upstash_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QStash signature required.",
        )

    raw = await request.body()
    try:
        QStashSignatureVerifier().verify(
            body=raw.decode("utf-8"),
            signature=upstash_signature,
            url=str(request.url),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash signature.",
        ) from exc

    try:
        message = DispatchPayload.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid dispatch payload.",
        ) from exc

    async with session_factory() as session:
        repo = WorkItemDispatchRepository(session)
        item = await repo.create(message.to_dispatch())
        await session.commit()
        organization_id = item.organization_id
        work_item_id = item.id

    queued = await request_runtime_execution(
        organization_id=organization_id,
        work_item_id=work_item_id,
        expected_step=0,
        reason="dispatch",
    )
    return {
        "status": "accepted",
        "workItemId": str(work_item_id),
        "executionQueued": queued is not None,
    }


@router.post("/execute", status_code=202)
async def execute(
    request: Request,
    background_tasks: BackgroundTasks,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    raw = await request.body()
    await _verify_caller(request, raw, upstash_signature)
    try:
        message = ExecutePayload.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid runtime execution payload.",
        ) from exc

    if not settings.runtime_execution_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Runtime execution is disabled.",
        )
    try:
        CutoverController(settings.cutover_stage).require_authoritative("runtime")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    # This delivery's usage. An accepted delivery continues counting in its background task;
    # one turned away is recorded here, since duplicate wake-ups are part of the cost.
    usage = Usage()
    with metered(usage):
        coordinator = RedisCoordinator.from_settings()
        lease, slot = await _claim(coordinator, message.work_item_id, message.expected_step)
    if lease is None or slot is None:
        await record_usage(usage, kind="runtime.delivery", organization_id=message.organization_id,
            work_item_id=message.work_item_id, step=message.expected_step,
            outcome="busy" if lease is None else "capacity_full")
    if lease is None:
        return {
            "status": "busy",
            "workItemId": str(message.work_item_id),
            "currentStep": message.expected_step,
        }
    if slot is None:
        raise HTTPException(429, "Runtime execution capacity is full; retry delivery.")
    # Work items already exist durably. The sweep recovers an acknowledged task
    # after process loss; Redis leases prevent duplicate execution across processes.
    background_tasks.add_task(_start_runtime_task, message, coordinator, lease, slot, usage)
    return {"status": "accepted", "workItemId": str(message.work_item_id), "currentStep": message.expected_step}


async def _claim(coordinator: RedisCoordinator, work_item_id: UUID, step: int) -> tuple[Lease | None, Lease | None]:
    """The step's lease and a free runtime slot. No lease: the step is already running elsewhere.
    No slot: capacity is full. The coordinator is closed unless both were claimed."""
    lease = await coordinator.acquire_lock(f"runtime:{work_item_id}:{step}", ttl_seconds=90)
    slot = None
    if lease is not None:
        for index in range(max(1, settings.runtime_qstash_parallelism)):
            slot = await coordinator.acquire_lock(f"runtime-slot:{settings.environment}:{index}", ttl_seconds=90)
            if slot:
                break
        if slot is None:
            await coordinator.release_lock(lease)
    if lease is None or slot is None:
        await coordinator.close()
    return lease, slot


async def run_here(organization_id: UUID, work_item_id: UUID, step: int, reason: str) -> str | None:
    """Run a due step in this serving process instead of sending a QStash wake-up (F11): "started",
    "busy" when the step already runs, or None when it cannot run here (no free slot, Redis or
    the cutover unavailable) and QStash should deliver it. Like an accepted QStash delivery, a
    step lost with the process is recovered by the sweep."""
    try:
        CutoverController(settings.cutover_stage).require_authoritative("runtime")
        coordinator = RedisCoordinator.from_settings()
        lease, slot = await _claim(coordinator, work_item_id, step)
    except (RuntimeError, RedisError):
        return None
    if lease is None:
        return "busy"
    if slot is None:
        return None
    message = ExecutePayload(organization_id=organization_id, work_item_id=work_item_id,
                             expected_step=step, reason=reason[:80])
    await _start_runtime_task(message, coordinator, lease, slot)
    return "started"


async def _metered_runtime_message(message, coordinator, lease, slot, usage):
    result = None
    try:
        with metered(usage):
            result = await _execute_runtime_message(message, coordinator, lease, slot)
        return result
    finally:
        outcome = "error" if result is None else str(result.get("state") or result.get("status"))
        with suppress(Exception):
            await record_usage(usage, kind="runtime.step", organization_id=message.organization_id,
                work_item_id=message.work_item_id, run_id=usage.labels.get("run_id"),
                step=message.expected_step, phase=usage.labels.get("phase"), outcome=outcome)


async def _start_runtime_task(message, coordinator, lease, slot, usage=None):
    task = asyncio.create_task(_metered_runtime_message(message, coordinator, lease, slot, usage or Usage()))
    _runtime_tasks.add(task)

    def finished(completed):
        _runtime_tasks.discard(completed)
        if not completed.cancelled() and completed.exception() is not None:
            logger.error("Background runtime interrupted work_item=%s error_type=%s; saved checkpoint remains eligible for recovery",
                message.work_item_id, type(completed.exception()).__name__)

    task.add_done_callback(finished)


async def stop_runtime_tasks(drain_seconds: float = 0):
    """Lets running steps finish for up to `drain_seconds`, then cancels the rest; a cancelled step
    keeps its checkpoint and the sweep recovers it. New steps no longer start here once serving
    has stopped, so their wake-ups go to QStash."""
    if _runtime_tasks and drain_seconds > 0:
        logger.warning("Draining %d running step(s) for up to %ss", len(_runtime_tasks), drain_seconds)
        await asyncio.wait(tuple(_runtime_tasks), timeout=drain_seconds)
    tasks = tuple(_runtime_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        logger.warning("Cancelled %d running step(s) at shutdown; the sweep will recover them", len(tasks))
        await asyncio.gather(*tasks, return_exceptions=True)


async def _durable_continuation(message: ExecutePayload, outcome: RuntimeStepOutcome) -> None:
    """The wake-up could not be sent: save it in the outbox, whose drain retries delivery (on
    commit, then on its schedule). The sweep remains the last backstop."""
    try:
        async with session_factory() as session:
            await TransactionalOutbox(session).enqueue(
                topic="run.progress", aggregate_type="run", aggregate_id=str(outcome.run_id or message.work_item_id),
                payload={"organization_id": str(message.organization_id), "work_item_id": str(message.work_item_id),
                         "run_id": str(outcome.run_id) if outcome.run_id else None,
                         "current_step": outcome.current_step, "continuation_phase": outcome.continuation_phase,
                         **({"queue_partition": outcome.queue_partition} if outcome.queue_partition else {})},
            )
            await session.commit()
    except Exception:
        logger.exception("Runtime continuation could not be saved work_item=%s; the sweep will recover it",
            message.work_item_id)


async def _latest_run(session, item: WorkItem) -> Run | None:
    return await session.scalar(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at.desc()).limit(1))


async def _not_runnable(session, item: WorkItem, message: "ExecutePayload") -> dict[str, object] | None:
    """Why this delivery runs nothing (not due, already ended, or waiting its turn), or None after
    admitting the item."""
    if item.scheduled_at > datetime.now(UTC):
        return {"status": "not_due", "workItemId": str(item.id), "scheduledAt": item.scheduled_at.isoformat(),
                "currentStep": _runtime_step(item)}
    if item.status in {"completed", "failed", "cancelled"}:
        return {"status": "noop", "workItemId": str(item.id), "state": item.status, "currentStep": _runtime_step(item)}
    if item.status != "queued":
        return None
    payload = dict(item.payload or {})
    runtime = dict(payload.get("runtime") or {})
    # Only a task that has never run is new work: one already under way is never stopped by a limit.
    first = not runtime.get("startedAt") and not runtime.get("admittedAt")
    if first:
        try:
            await entitlements.require_allowance(session, item.organization_id, *entitlements.TASK_ALLOWANCES)
        except entitlements.EntitlementDenied as denied:
            return await _not_started(session, item, denied.message)
    if await admit_due_item(session, item, now=datetime.now(UTC)):
        runtime["queueState"] = "admitted"
        runtime["waitingReason"] = None
        runtime.pop("admissionWaits", None)
        if first:
            runtime["admittedAt"] = datetime.now(UTC).isoformat()
            count_plan_usage(tasks=1)  # saved into the plan period with the step's usage
        item.payload = {**payload, "runtime": runtime}
        await session.commit()
        return None
    await session.refresh(item)
    if item.status != "queued":
        return {"status": "noop", "workItemId": str(item.id), "state": item.status, "currentStep": _runtime_step(item)}
    payload = dict(item.payload or {})
    runtime = dict(payload.get("runtime") or {})
    waits = int(runtime.get("admissionWaits") or 0)
    wait_seconds = admission_wait_seconds(waits)
    item.scheduled_at = datetime.now(UTC) + timedelta(seconds=wait_seconds)
    runtime.update(admissionWaits=waits + 1, queueState="queued",
                   waitingReason="Waiting for an earlier or higher-priority Work Item.")
    item.payload = {**payload, "runtime": runtime}
    await session.commit()
    await request_runtime_execution(organization_id=item.organization_id, work_item_id=item.id,
                                    expected_step=message.expected_step, reason=f"admission-wait:{waits}",
                                    delay_seconds=wait_seconds)
    return {"status": "queued", "workItemId": str(item.id), "state": "queued", "currentStep": message.expected_step,
            "summary": runtime["waitingReason"]}


async def _not_started(session, item: WorkItem, reason: str) -> dict[str, object]:
    """The organisation's plan does not allow this new task: it ends without running, saying why."""
    item.status = "failed"
    payload = dict(item.payload or {})
    runtime = dict(payload.get("runtime") or {})
    runtime.update(queueState="not_started", failureCategory="plan_limit", waitingReason=reason)
    payload.update(runtime=runtime, lastError=reason, completedAt=datetime.now(UTC).isoformat())
    item.payload = payload
    await notify_task_conversation(session, item, key="plan-limit", content=reason)
    await session.commit()
    return {"status": "failed", "workItemId": str(item.id), "state": "failed", "currentStep": 0,
            "summary": reason, "failureCategory": "plan_limit"}


async def _execute_runtime_message(message: ExecutePayload, coordinator: RedisCoordinator,
    lease: Lease, slot: Lease) -> dict[str, object]:
    owner = asyncio.current_task()
    heartbeat = asyncio.create_task(keep_leases(coordinator, (lease, slot), owner))

    outcome = None
    continuation_delay_seconds = 0
    continuation_reason = "continuation"
    try:
        async with session_factory() as session:
            item = await session.scalar(
                select(WorkItem).where(
                    WorkItem.organization_id == message.organization_id,
                    WorkItem.id == message.work_item_id,
                )
            )
            if item is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Work item not found.",
                )
            skipped = await _not_runnable(session, item, message)
            if skipped is not None:
                return skipped
            try:
                outcome = await ManagedRuntimeExecutor(session).execute_step(
                    item=item,
                    expected_step=message.expected_step,
                )
                if outcome.state == "continue" and outcome.continuation_phase is not None:
                    # This is the step's only wake-up, sent once the lease is released so
                    # it cannot arrive while this phase still holds it. The step's outbox
                    # event is marked "wakeup": "runtime" and only updates the workspace.
                    continuation_reason = f"continuation-{outcome.continuation_phase}:background:{outcome.run_id}"
                if outcome.state == "continue" and item.scheduled_at > datetime.now(UTC):
                    continuation_delay_seconds = max(1, ceil((item.scheduled_at - datetime.now(UTC)).total_seconds()))
                    continuation_reason = f"provider-retry:{int(item.scheduled_at.timestamp())}"
            except AIProviderError as exc:
                return await _planning_failed(session, message, exc)
            except RuntimeError as exc:
                return await _runtime_stopped(session, message, exc)
            except Exception as exc:  # noqa: BLE001 - acknowledged jobs must expose an actionable wait
                return await _runtime_interrupted(session, message, exc)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        await coordinator.release_lock(lease)
        await coordinator.release_lock(slot)
        await coordinator.close()

    assert outcome is not None
    continuation_id = None
    if outcome.state == "continue":
        continuation_id = await request_runtime_execution(
            organization_id=message.organization_id,
            work_item_id=message.work_item_id,
            expected_step=outcome.current_step,
            reason=continuation_reason,
            delay_seconds=(
                continuation_delay_seconds if continuation_delay_seconds > 0 else None
            ),
            queue_partition=outcome.queue_partition,
        )
        if continuation_id is None and outcome.continuation_phase is not None:
            await _durable_continuation(message, outcome)

    return {
        "status": "ok",
        "workItemId": str(outcome.work_item_id),
        "runId": str(outcome.run_id) if outcome.run_id is not None else None,
        "state": outcome.state,
        "currentStep": outcome.current_step,
        "summary": outcome.summary,
        "continuationQueued": continuation_id is not None,
    }


SMART_PLANNER_WAITS = {
    "quota_exhausted": "Planning has reached its usage allowance. Your task and completed work are saved.",
    "rate_limited": "Planning is waiting for available capacity. Your task and completed work are saved.",
    "context_too_large": "The required task evidence exceeds the available planning limits. Your task and completed work are saved.",
}
PLANNING_TURN = "Waiting for its turn to plan: another task is planning now. Your task and completed work are saved."
FAILURE_CATEGORIES = {
    "authentication_failed": "authentication_expiry", "configuration_missing": "internal_platform_failure",
    "model_not_found": "internal_platform_failure", "quota_exhausted": "usage_limit", "rate_limited": "usage_limit",
    "context_too_large": "planning_context_limit", "content_rejected": "planning_refusal",
    "invalid_provider_response": "provider_response_invalid",
}


def _planning_wait(exc: AIProviderError, retry_count: int, *, preparing: bool, now: datetime):
    """What a planning failure means for the task: its state, the person's message, and when (if
    at all) it retries by itself. Returns (state, summary, retry_count, retry_at)."""
    if exc.category in {"configuration_missing", "authentication_failed", "model_not_found"}:
        from sqlalchemy.exc import SQLAlchemyError
        return ("waiting_configuration",
                "Task preparation is blocked by database setup. An administrator must complete the required setup before this work can continue."
                if isinstance(exc.__cause__, SQLAlchemyError) else "AI configuration is required before this work can continue.",
                retry_count, None)
    if exc.budget_scope == "concurrency":
        # Another task holds the planning slots; slots end with their call (or within 90 s of a
        # stopped process), so wait without spending one of the task's retries (F27).
        return ("waiting_ai", PLANNING_TURN, retry_count,
                now + timedelta(seconds=max(15, exc.retry_after_seconds or 0)))
    if exc.retryable and retry_count < settings.ai_max_retries:
        retry_count += 1
        delay_seconds = max(min(300, 15 * (2 ** (retry_count - 1))), exc.retry_after_seconds or 0)
        summary = ("Task preparation is temporarily unavailable; retry is scheduled." if preparing
                   else SMART_PLANNER_WAITS.get(exc.category, "AI planner is temporarily unavailable; retry is scheduled.")
                   if settings.smart_planner_enabled else "AI planner is temporarily unavailable; retry is scheduled.")
        return "waiting_ai", summary, retry_count, now + timedelta(seconds=delay_seconds)
    if preparing:
        return "waiting_ai", "Task preparation is unavailable; no external action was taken.", retry_count, None
    if settings.smart_planner_enabled and exc.category in SMART_PLANNER_WAITS:
        # Its automatic retries are used up: nothing wakes it by itself, so say how to continue.
        return "waiting_ai", SMART_PLANNER_WAITS[exc.category] + " Press Retry planning to try again.", retry_count, None
    # Paused: say why in plain words and how to continue (F14).
    return "waiting_ai", planning_paused(exc.category), retry_count, None


async def _planning_failed(session, message: "ExecutePayload", exc: AIProviderError) -> dict[str, object]:
    """Record a planning failure: retry by itself when it may pass, otherwise pause with a message
    (and, for a paused task, a Retry planning control) in its conversation."""
    item = await _recover_runtime_item(session, message)
    # Keep provider bodies and prompts out of logs; record the category
    # so transport failures can be distinguished from contract errors.
    logger.warning(
        "Runtime planning failure work_item=%s category=%s retryable=%s "
        "http_status=%s retry_after_seconds=%s error_type=%s",
        item.id, exc.category, exc.retryable, exc.status_code,
        exc.retry_after_seconds, type(exc.__cause__ or exc).__name__,
    )
    now = datetime.now(UTC)
    payload = dict(item.payload or {})
    runtime_meta = dict(payload["runtime"]) if isinstance(payload.get("runtime"), dict) else {}
    state, summary, retry_count, retry_at = _planning_wait(
        exc, max(0, int(runtime_meta.get("aiRetryCount", 0))),
        preparing=runtime_meta.get("plannerPhase") == "preparing_tools", now=now)
    retry_scheduled = retry_at is not None
    item.status = "queued" if retry_scheduled else state
    if retry_scheduled:
        item.scheduled_at = retry_at
    if settings.smart_planner_enabled and runtime_meta.get("plannerPhase") != "preparing_tools":
        runtime_meta["plannerStatus"] = "retrying" if retry_scheduled else state
    runtime_meta.update(
        aiRetryCount=retry_count, queueState="retrying" if retry_scheduled else state, waitingReason=summary,
        lastAIErrorCategory=exc.category, lastAIErrorAt=now.isoformat(),
        failureCategory=FAILURE_CATEGORIES.get(exc.category, "internal_platform_failure"
                                               if runtime_meta.get("plannerPhase") == "preparing_tools" else "provider_model_outage"),
        plannerFailurePhase=runtime_meta.get("plannerPhase"), plannerPhase=state, plannerPhaseStartedAt=None)
    logger.warning("Runtime planning recovery work_item=%s phase=%s timing=%s",
        message.work_item_id, runtime_meta.get("plannerFailurePhase"), runtime_meta.get("plannerTiming"))
    if retry_at is not None:
        runtime_meta["aiRetryAt"] = retry_at.isoformat()
    else:
        runtime_meta.pop("aiRetryAt", None)
    payload["runtime"] = runtime_meta
    payload["lastError"] = {"kind": "ai_provider", "category": exc.category, "retryable": exc.retryable}
    item.payload = payload
    run = await _latest_run(session, item)
    if run is not None and run.status not in {"completed", "failed", "cancelled"}:
        run.status = state
        run.result_summary = summary
    paused = not retry_scheduled and state == "waiting_ai" and run is not None
    await notify_integration_work(
        session, item, key=f"planning-{'retry' if retry_scheduled else state}:{_runtime_step(item)}:{retry_count}",
        content=summary, references={"type": "work_item", "id": str(item.id), "runId": str(run.id),
                                     "controls": "retry_planning"} if paused and run is not None else None)
    await session.commit()
    retry_dispatch_id = None
    if retry_at is not None:
        retry_dispatch_id = await request_runtime_execution(
            organization_id=item.organization_id, work_item_id=item.id, expected_step=_runtime_step(item),
            reason=f"ai-provider-retry:{int(retry_at.timestamp())}",
            delay_seconds=max(1, ceil((retry_at - datetime.now(UTC)).total_seconds())))
    return {
        "status": state, "workItemId": str(item.id), "state": state, "currentStep": _runtime_step(item),
        "summary": summary, "aiErrorCategory": exc.category, "failureCategory": runtime_meta["failureCategory"],
        "retryScheduled": retry_scheduled, "retryAt": retry_at.isoformat() if retry_at is not None else None,
        "retryDispatchQueued": retry_dispatch_id is not None,
    }


async def _runtime_stopped(session, message: "ExecutePayload", exc: RuntimeError) -> dict[str, object]:
    item = await _recover_runtime_item(session, message)
    failure_category = _runtime_failure_category(str(exc))
    logger.error("Runtime stopped work_item=%s category=%s error_type=%s", item.id, failure_category, type(exc).__name__)
    item.status = "failed"
    payload = dict(item.payload or {})
    runtime_meta = dict(payload["runtime"]) if isinstance(payload.get("runtime"), dict) else {}
    runtime_meta["failureCategory"] = failure_category
    payload.update(runtime=runtime_meta, lastError=str(exc)[:1000], completedAt=datetime.now(UTC).isoformat())
    item.payload = payload
    run = await _latest_run(session, item)
    if run is not None and run.status not in {"completed", "failed", "cancelled"}:
        run.status = "failed"
        run.result_summary = str(exc)[:4000]
    await notify_integration_work(
        session, item, key=f"runtime-stopped:{_runtime_step(item)}",
        content="Work stopped because the pending step could not be completed. Your request and completed steps remain saved. See the process record for the saved outcome.",
    )
    await session.commit()
    return {"status": "failed", "workItemId": str(item.id), "state": "failed", "currentStep": _runtime_step(item),
            "summary": str(exc), "failureCategory": failure_category}


async def _runtime_interrupted(session, message: "ExecutePayload", exc: Exception) -> dict[str, object]:
    item = await _recover_runtime_item(session, message)
    run = await _latest_run(session, item)
    meta = dict((item.payload or {}).get("runtime") or {})
    planning = run is None or meta.get("plannerPhase") in {"preparing_tools", "awaiting_ai"}
    state = "waiting_configuration" if planning else "uncertain_outcome"
    summary = ("Task preparation was interrupted by an internal error. Completed work is saved; this needs attention before continuing."
        if planning else "Execution was interrupted and its outcome needs verification. Completed work is saved; the action will not be replayed automatically.")
    item.status = state
    item.payload = {**item.payload, "runtime": {**meta, "queueState": state,
        "waitingReason": summary, "failureCategory": "internal_platform_failure"}}
    if run is not None and run.status not in {"completed", "failed", "cancelled"}:
        run.status, run.result_summary = state, summary
    await notify_integration_work(session, item, key=f"runtime-interrupted:{_runtime_step(item)}", content=summary)
    await session.commit()
    logger.error("Runtime interrupted work_item=%s error_type=%s state=%s at=%s",
        message.work_item_id, type(exc).__name__, state, app_frames(exc))
    return {"status": state, "workItemId": str(item.id), "summary": summary}


@router.post("/schedule-due")
@metered_endpoint("runtime.schedule_due")
async def schedule_due(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    """An on-time schedule wake-up: queue the job's due run. If the job was changed, paused or
    already queued, nothing happens; if QStash could not delay this far, wait again."""
    raw = await request.body()
    _verify_qstash(request, raw, upstash_signature)
    try:
        message = ScheduleDuePayload.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid schedule payload.") from exc
    if not settings.runtime_execution_enabled:
        return {"status": "disabled", "queued": 0}
    due = message.due if message.due.tzinfo else message.due.replace(tzinfo=UTC)
    early = (due - datetime.now(UTC)).total_seconds()
    if early > SCHEDULE_EARLY_WAIT_SECONDS:
        from audoryn_shared.runtime.schedule_wake import arm_schedule

        await arm_schedule(organization_id=message.organization_id, job_id=message.job_id, due=due)
        return {"status": "rearmed", "queued": 0}
    if early > 0:
        await asyncio.sleep(early)
    async with session_factory() as session:
        queued = await queue_due_schedules(session, now=datetime.now(UTC), limit=1, job_id=message.job_id)
    return {"status": "ok", "queued": queued}


@router.post("/sweep")
@metered_endpoint("runtime.sweep")
async def sweep(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    raw = await request.body()
    _verify_qstash(request, raw, upstash_signature)
    return await run_sweep()


async def _decided_approval_items(session, waiting: list[WorkItem]) -> set[UUID]:
    """The waiting tasks whose current step's approval has been decided (approved, or rejected so
    the step can close; F28), read in three queries for all of them."""
    if not waiting:
        return set()
    runs = (await session.scalars(select(Run).where(Run.work_item_id.in_([item.id for item in waiting]))
                                  .order_by(Run.created_at.desc()))).all()
    latest: dict[UUID, Run] = {}
    for run in runs:
        latest.setdefault(run.work_item_id, run)
    steps = (await session.scalars(select(RunStep).where(
        RunStep.run_id.in_([run.id for run in latest.values()])))).all() if latest else []
    by_step = {(step.run_id, step.step_index): step for step in steps}
    approval_of: dict[UUID, UUID] = {}
    for item in waiting:
        run = latest.get(item.id)
        step = by_step.get((run.id, _runtime_step(item))) if run else None
        raw = step.output.get("approvalId") if step and isinstance(step.output, dict) else None
        with suppress(ValueError):
            if raw:
                approval_of[item.id] = UUID(str(raw))
    if not approval_of:
        return set()
    statuses = dict((await session.execute(select(Approval.id, Approval.status).where(
        Approval.id.in_(set(approval_of.values()))))).all())
    return {item_id for item_id, approval in approval_of.items() if statuses.get(approval) in {"approved", "rejected"}}


async def run_sweep() -> dict[str, object]:
    """Recovers due work: the scheduled sweep, and the backend's own delivery watchdog."""
    # Housekeeping that used to run on every outbox drain.
    from audoryn_shared.runtime.outbox_publisher import run_periodic_maintenance
    await run_periodic_maintenance()
    if not settings.runtime_execution_enabled:
        return {"status": "disabled", "scanned": 0, "queued": 0}

    try:
        CutoverController(settings.cutover_stage).require_authoritative("runtime")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    expired_browser_sessions = await browser_provider.reap_expired_sessions()
    now = datetime.now(UTC)
    from audoryn_execution.application.services.approval_expiry import (
        expire_and_remind,
        wake_expired,
    )

    async with session_factory() as session:
        expired_runs = await expire_and_remind(session, now=now)
    await wake_expired(expired_runs)
    async with session_factory() as session:
        scheduled_queued = await queue_due_schedules(
            session,
            now=now,
            limit=settings.runtime_sweep_limit,
        )
        items = list(
            (
                await session.scalars(
                    select(WorkItem)
                    .where(
                        WorkItem.status.in_(["queued", "admitted", "running", "waiting_approval"]),
                        WorkItem.scheduled_at <= now,
                    )
                    .order_by(WorkItem.scheduled_at, WorkItem.id)
                    .limit(settings.runtime_sweep_limit)
                )
            ).all()
        )

        decided = await _decided_approval_items(
            session, [item for item in items if item.status == "waiting_approval"])
        candidates = [(item.organization_id, item.id, _runtime_step(item)) for item in items
                      if item.status != "waiting_approval" or item.id in decided]

    queued = 0
    for organization_id, work_item_id, expected_step in candidates:
        message_id = await request_runtime_execution(
            organization_id=organization_id,
            work_item_id=work_item_id,
            expected_step=expected_step,
            reason="recovery",
        )
        if message_id is not None:
            queued += 1

    return {
        "status": "ok",
        "scanned": len(items),
        "eligible": len(candidates),
        "queued": queued,
        "scheduledQueued": scheduled_queued,
        "expiredBrowserSessions": expired_browser_sessions,
    }
