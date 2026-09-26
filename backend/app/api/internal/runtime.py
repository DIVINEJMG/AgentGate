import base64
import binascii
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.jobs_routes import current_revision, queue_due_schedules, queue_job
from app.application.services.browser_origin_authority import (
    reconcile_ai_job_browser_origins,
)
from app.application.services.cutover import CutoverController
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIProviderError
from app.domain.identity.principals import HumanPrincipal
from app.domain.jobs.dispatch import ScheduledDispatch
from app.infrastructure.database.dispatch import WorkItemDispatchRepository
from app.infrastructure.database.models import (
    Approval,
    Job,
    QueueDeliveryFailure,
    Run,
    RunStep,
    WorkItem,
)
from app.infrastructure.database.session import session_factory
from app.infrastructure.qstash.verifier import QStashSignatureVerifier
from app.infrastructure.redis.coordination import RedisCoordinator
from app.infrastructure.storage.provider import object_storage_from_settings
from app.runtime.managed import ManagedRuntimeExecutor
from app.runtime.qstash_trigger import request_runtime_execution

router = APIRouter(prefix="/internal/v1/runtime", tags=["internal-runtime"])
logger = logging.getLogger(__name__)


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


def _runtime_step(item: WorkItem) -> int:
    payload = item.payload if isinstance(item.payload, dict) else {}
    raw = payload.get("runtime")
    runtime = raw if isinstance(raw, dict) else {}
    return max(0, int(runtime.get("currentStep", 0)))


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


@router.post("/execute")
async def execute(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    raw = await request.body()
    _verify_qstash(request, raw, upstash_signature)
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

    coordinator = RedisCoordinator.from_settings()
    lease = await coordinator.acquire_lock(
        f"runtime:{message.work_item_id}:{message.expected_step}",
        ttl_seconds=settings.runtime_delivery_timeout_seconds + 30,
    )
    if lease is None:
        await coordinator.close()
        return {
            "status": "busy",
            "workItemId": str(message.work_item_id),
            "currentStep": message.expected_step,
        }

    outcome = None
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
            if item.scheduled_at > datetime.now(UTC):
                return {
                    "status": "not_due",
                    "workItemId": str(item.id),
                    "scheduledAt": item.scheduled_at.isoformat(),
                    "currentStep": _runtime_step(item),
                }
            if item.status in {"completed", "failed", "cancelled"}:
                return {
                    "status": "noop",
                    "workItemId": str(item.id),
                    "state": item.status,
                    "currentStep": _runtime_step(item),
                }
            try:
                outcome = await ManagedRuntimeExecutor(session).execute_step(
                    item=item,
                    expected_step=message.expected_step,
                )
            except AIProviderError as exc:
                now = datetime.now(UTC)
                payload = dict(item.payload or {})
                raw_runtime = payload.get("runtime")
                runtime_meta = (
                    dict(raw_runtime)
                    if isinstance(raw_runtime, dict)
                    else {}
                )
                retry_count = max(0, int(runtime_meta.get("aiRetryCount", 0)))
                configuration_error = exc.category in {
                    "configuration_missing",
                    "authentication_failed",
                    "model_not_found",
                }
                retry_scheduled = False
                retry_at: datetime | None = None

                if configuration_error:
                    state = "waiting_configuration"
                    item.status = state
                    summary = "AI configuration is required before this work can continue."
                elif exc.retryable and retry_count < settings.ai_max_retries:
                    retry_count += 1
                    delay_seconds = min(300, 15 * (2 ** (retry_count - 1)))
                    retry_at = now + timedelta(seconds=delay_seconds)
                    item.status = "queued"
                    item.scheduled_at = retry_at
                    state = "waiting_ai"
                    retry_scheduled = True
                    summary = "AI planner is temporarily unavailable; retry is scheduled."
                elif exc.category == "invalid_provider_response":
                    state = "waiting_ai"
                    item.status = state
                    summary = (
                        "AI planner returned a response that did not satisfy the "
                        "required structured contract; no external action was taken."
                    )
                else:
                    state = "waiting_ai"
                    item.status = state
                    summary = "AI planner is unavailable; no external action was taken."

                runtime_meta["aiRetryCount"] = retry_count
                runtime_meta["lastAIErrorCategory"] = exc.category
                runtime_meta["failureCategory"] = (
                    "authentication_expiry"
                    if exc.category == "authentication_failed"
                    else "internal_platform_failure"
                    if exc.category in {"configuration_missing", "model_not_found"}
                    else "provider_response_invalid"
                    if exc.category == "invalid_provider_response"
                    else "provider_model_outage"
                )
                runtime_meta["lastAIErrorAt"] = now.isoformat()
                if retry_at is not None:
                    runtime_meta["aiRetryAt"] = retry_at.isoformat()
                payload["runtime"] = runtime_meta
                payload["lastError"] = {
                    "kind": "ai_provider",
                    "category": exc.category,
                    "retryable": exc.retryable,
                }
                item.payload = payload

                run = await session.scalar(
                    select(Run)
                    .where(Run.work_item_id == item.id)
                    .order_by(Run.created_at.desc())
                    .limit(1)
                )
                if run is not None and run.status not in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    run.status = state
                    run.result_summary = summary
                await session.commit()
                return {
                    "status": state,
                    "workItemId": str(item.id),
                    "state": state,
                    "currentStep": _runtime_step(item),
                    "summary": summary,
                    "aiErrorCategory": exc.category,
                    "failureCategory": runtime_meta["failureCategory"],
                    "retryScheduled": retry_scheduled,
                    "retryAt": retry_at.isoformat() if retry_at is not None else None,
                }
            except RuntimeError as exc:
                item.status = "failed"
                payload = dict(item.payload or {})
                failure_category = _runtime_failure_category(str(exc))
                raw_runtime = payload.get("runtime")
                runtime_meta = dict(raw_runtime) if isinstance(raw_runtime, dict) else {}
                runtime_meta["failureCategory"] = failure_category
                payload["runtime"] = runtime_meta
                payload["lastError"] = str(exc)[:1000]
                payload["completedAt"] = datetime.now(UTC).isoformat()
                item.payload = payload
                run = await session.scalar(
                    select(Run)
                    .where(Run.work_item_id == item.id)
                    .order_by(Run.created_at.desc())
                    .limit(1)
                )
                if run is not None and run.status not in {"completed", "failed", "cancelled"}:
                    run.status = "failed"
                    run.result_summary = str(exc)[:4000]
                await session.commit()
                return {
                    "status": "failed",
                    "workItemId": str(item.id),
                    "state": "failed",
                    "currentStep": _runtime_step(item),
                    "summary": str(exc),
                    "failureCategory": failure_category,
                }
    finally:
        await coordinator.release_lock(lease)
        await coordinator.close()

    assert outcome is not None
    continuation_id = None
    if outcome.state == "continue":
        continuation_id = await request_runtime_execution(
            organization_id=message.organization_id,
            work_item_id=message.work_item_id,
            expected_step=outcome.current_step,
            reason="continuation",
        )

    return {
        "status": "ok",
        "workItemId": str(outcome.work_item_id),
        "runId": str(outcome.run_id),
        "state": outcome.state,
        "currentStep": outcome.current_step,
        "summary": outcome.summary,
        "continuationQueued": continuation_id is not None,
    }


@router.post("/sweep")
async def sweep(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    raw = await request.body()
    _verify_qstash(request, raw, upstash_signature)
    if not settings.runtime_execution_enabled:
        return {"status": "disabled", "scanned": 0, "queued": 0}

    try:
        CutoverController(settings.cutover_stage).require_authoritative("runtime")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    now = datetime.now(UTC)
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
                        WorkItem.status.in_(["queued", "running", "waiting_approval"]),
                        WorkItem.scheduled_at <= now,
                    )
                    .order_by(WorkItem.scheduled_at, WorkItem.id)
                    .limit(settings.runtime_sweep_limit)
                )
            ).all()
        )

        candidates: list[tuple[UUID, UUID, int]] = []
        for item in items:
            if item.status == "waiting_approval":
                run = await session.scalar(
                    select(Run)
                    .where(Run.work_item_id == item.id)
                    .order_by(Run.created_at.desc())
                    .limit(1)
                )
                if run is None:
                    continue
                step = await session.scalar(
                    select(RunStep).where(
                        RunStep.run_id == run.id,
                        RunStep.step_index == _runtime_step(item),
                    )
                )
                output = (
                    dict(step.output or {})
                    if step is not None and isinstance(step.output, dict)
                    else {}
                )
                raw_approval_id = output.get("approvalId")
                if not raw_approval_id:
                    continue
                try:
                    approval_id = UUID(str(raw_approval_id))
                except ValueError:
                    continue
                approval = await session.get(Approval, approval_id)
                if approval is None or approval.status != "approved":
                    continue
            candidates.append((item.organization_id, item.id, _runtime_step(item)))

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
    }
