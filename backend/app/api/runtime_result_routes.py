from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.jobs_routes import work_item_public, work_item_v2
from app.api.product_common import (
    append_audit,
    latest_setting,
    not_found,
    require_permission,
    utcnow,
)
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.execution.provenance import ExecutionProvenance, provenance_from_action_payload
from app.infrastructure.database.models import (
    Action,
    Artifact,
    AuditEvent,
    Job,
    Memory,
    Result,
    ResultExport,
    ResultVersion,
    Run,
    RunStep,
    Worker,
    WorkItem,
)
from app.infrastructure.database.outbox import TransactionalOutbox
from app.infrastructure.database.session import database_session
from app.infrastructure.storage.provider import object_storage_from_settings

v1_router = APIRouter(tags=["runtime", "memory", "results"])
v2_router = APIRouter(tags=["runtime", "memory", "results"])


async def _run_public(
    session: AsyncSession, run: Run
) -> dict[str, Any]:
    item = await session.get(WorkItem, run.work_item_id)
    job = await session.get(Job, item.job_id) if item else None
    worker = await session.get(Worker, job.worker_id) if job else None
    steps = list(
        (
            await session.scalars(
                select(RunStep)
                .where(RunStep.run_id == run.id)
                .order_by(RunStep.step_index)
            )
        ).all()
    )
    artifacts = list(
        (
            await session.scalars(
                select(Artifact).where(Artifact.run_id == run.id)
            )
        ).all()
    )
    raw_runtime = (
        item.payload.get("runtime")
        if item is not None and isinstance(item.payload, dict)
        else None
    )
    meta: dict[str, Any] = (
        dict(raw_runtime) if isinstance(raw_runtime, dict) else {}
    )
    return {
        "id": str(run.id),
        "organizationId": str(run.organization_id),
        "workItemId": str(run.work_item_id),
        "jobId": str(job.id) if job else "",
        "workerId": str(worker.id) if worker else "",
        "agentId": str(worker.agent_identity_id) if worker else "",
        "status": run.status,
        "attempt": int(meta.get("attempt", 1)),
        "currentStep": int(meta.get("currentStep", 0)),
        "stepIds": [str(step.id) for step in steps],
        "contextMemoryIds": list(meta.get("contextMemoryIds", [])),
        "artifactIds": [str(artifact.id) for artifact in artifacts],
        "planSummary": str(meta.get("planSummary", "")),
        "resultSummary": run.result_summary,
        "failure": meta.get("failure"),
        "cancellationReason": meta.get("cancellationReason"),
        "cancelledBy": meta.get("cancelledBy"),
        "cancelledAt": meta.get("cancelledAt"),
        "correlationId": run.correlation_id,
        "createdAt": run.created_at.isoformat(),
        "startedAt": meta.get("startedAt"),
        "completedAt": meta.get("completedAt"),
        "updatedAt": run.updated_at.isoformat(),
    }


def _run_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": public["id"],
            "status": public["status"],
            "attempt": public["attempt"],
            "createdAt": public["createdAt"],
            "startedAt": public["startedAt"],
            "completedAt": public["completedAt"],
            "updatedAt": public["updatedAt"],
        },
        "organizationId": public["organizationId"],
        "assignment": {
            "workItemId": public["workItemId"],
            "jobId": public["jobId"],
            "workerId": public["workerId"],
            "agentId": public["agentId"],
        },
        "execution": {
            "currentStep": public["currentStep"],
            "stepIds": public["stepIds"],
            "contextMemoryIds": public["contextMemoryIds"],
            "artifactIds": public["artifactIds"],
            "planSummary": public["planSummary"],
            "resultSummary": public["resultSummary"],
            "failure": public["failure"],
            "cancellation": {
                "reason": public["cancellationReason"],
                "by": public["cancelledBy"],
                "at": public["cancelledAt"],
            },
        },
        "trace": {"correlationId": public["correlationId"]},
    }


def _step_public(step: RunStep) -> dict[str, Any]:
    inp = step.input if isinstance(step.input, dict) else {}
    out = step.output if isinstance(step.output, dict) else {}
    return {
        "id": str(step.id),
        "runId": str(step.run_id),
        "index": step.step_index,
        "kind": step.kind,
        "title": str(inp.get("title", "")),
        "instruction": str(inp.get("instruction", "")),
        "resourceId": str(inp.get("resourceId", "")),
        "scope": str(inp.get("scope", "")),
        "status": step.status,
        "output": out.get("output"),
        "actionId": out.get("actionId"),
        "approvalId": out.get("approvalId"),
        "error": out.get("error"),
        "startedAt": out.get("startedAt"),
        "completedAt": out.get("completedAt"),
    }


def _step_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": public["id"],
            "index": public["index"],
            "kind": public["kind"],
            "status": public["status"],
        },
        "request": {
            "title": public["title"],
            "instruction": public["instruction"],
            "resourceId": public["resourceId"] or None,
            "scope": public["scope"] or None,
        },
        "execution": {
            "output": public["output"],
            "actionId": public["actionId"],
            "approvalId": public["approvalId"],
            "error": public["error"],
            "startedAt": public["startedAt"],
            "completedAt": public["completedAt"],
        },
    }


@v1_router.get("/organizations/{organization_id}/runtime")
async def runtime_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.read")
    runs = list(
        (
            await session.scalars(
                select(Run)
                .where(Run.organization_id == organization_id)
                .order_by(desc(Run.created_at))
                .limit(200)
            )
        ).all()
    )
    public = [await _run_public(session, run) for run in runs]
    counts = Counter(run.status for run in runs)
    return {
        "runs": public,
        "summary": {
            "total": len(runs),
            "queued": counts["queued"],
            "running": counts["running"],
            "waitingApproval": counts["waiting_approval"],
            "completed": counts["completed"],
            "failed": counts["failed"],
            "cancelled": counts["cancelled"],
        },
        "window": {"limit": 200, "truncated": len(runs) >= 200},
        "runtime": {
            "executionEnabled": settings.runtime_execution_enabled,
            "mode": "worker" if settings.runtime_execution_enabled else "fail_closed",
        },
    }


@v2_router.get("/organizations/{organization_id}/runtime")
async def runtime_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await runtime_v1(organization_id, principal, session)
    return {
        "data": {
            "runs": [_run_v2(item) for item in result["runs"]],
            "summary": result["summary"],
            "window": result["window"],
            "runtime": result["runtime"],
        }
    }


@v1_router.get("/organizations/{organization_id}/runs/{run_id}")
async def run_detail_v1(
    organization_id: UUID,
    run_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.read")
    run = await session.scalar(
        select(Run).where(
            Run.organization_id == organization_id, Run.id == run_id
        )
    )
    if run is None:
        raise not_found("Run")
    steps = list(
        (
            await session.scalars(
                select(RunStep)
                .where(RunStep.run_id == run.id)
                .order_by(RunStep.step_index)
            )
        ).all()
    )
    return {
        "run": await _run_public(session, run),
        "steps": [_step_public(step) for step in steps],
    }


@v2_router.get("/organizations/{organization_id}/runs/{run_id}")
async def run_detail_v2(
    organization_id: UUID,
    run_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await run_detail_v1(
        organization_id, run_id, principal, session
    )
    return {
        "data": {
            "run": _run_v2(result["run"]),
            "steps": [_step_v2(step) for step in result["steps"]],
        }
    }


async def _runtime_disabled() -> None:
    if not settings.runtime_execution_enabled:
        raise HTTPException(
            409,
            "Runtime execution is disabled. Work remains safely queued until runtime cutover is enabled.",
        )


@v1_router.post("/organizations/{organization_id}/work-items/{work_item_id}/process", status_code=202)
async def process_item_v1(
    organization_id: UUID,
    work_item_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.run")
    await _runtime_disabled()
    item = await session.scalar(
        select(WorkItem).where(
            WorkItem.organization_id == organization_id,
            WorkItem.id == work_item_id,
        )
    )
    if item is None:
        raise not_found("Work item")
    run = await session.scalar(
        select(Run)
        .where(Run.work_item_id == item.id)
        .order_by(desc(Run.created_at))
        .limit(1)
    )
    created_run = run is None
    if run is None:
        run = Run(
            organization_id=organization_id,
            work_item_id=item.id,
            status="queued",
            correlation_id=item.correlation_id,
        )
        session.add(run)
        await session.flush()
    item.status = "queued"
    if created_run:
        await TransactionalOutbox(session).enqueue(
            topic="run.created",
            aggregate_type="run",
            aggregate_id=str(run.id),
            payload={
                "organization_id": str(organization_id),
                "run_id": str(run.id),
                "job_id": str(item.job_id),
                "correlation_id": item.correlation_id,
                "status": run.status,
            },
        )
    await session.commit()
    return {
        "run": await _run_public(session, run),
        "workItem": await work_item_public(session, item),
        "waitingForApproval": False,
        "retryScheduled": False,
    }


@v2_router.post("/organizations/{organization_id}/work-items/{work_item_id}/process", status_code=202)
async def process_item_v2(
    organization_id: UUID,
    work_item_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await process_item_v1(
        organization_id, work_item_id, principal, session
    )
    run = await session.get(Run, UUID(result["run"]["id"]))
    item = await session.get(WorkItem, work_item_id)
    assert run is not None and item is not None
    return {
        "data": {
            "run": _run_v2(await _run_public(session, run)),
            "workItem": await work_item_v2(session, item),
            "waitingForApproval": False,
            "retryScheduled": False,
        }
    }


@v1_router.post("/organizations/{organization_id}/runs/{run_id}/continue", status_code=202)
async def continue_run_v1(
    organization_id: UUID,
    run_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    await _runtime_disabled()
    run = await session.scalar(
        select(Run).where(
            Run.organization_id == organization_id, Run.id == run_id
        )
    )
    if run is None:
        raise not_found("Run")
    item = await session.get(WorkItem, run.work_item_id)
    if item is None:
        raise HTTPException(500, "Run work item is unavailable.")
    return {
        "run": await _run_public(session, run),
        "workItem": await work_item_public(session, item),
        "waitingForApproval": run.status == "waiting_approval",
        "retryScheduled": False,
    }


@v2_router.post("/organizations/{organization_id}/runs/{run_id}/continue", status_code=202)
async def continue_run_v2(
    organization_id: UUID,
    run_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await continue_run_v1(
        organization_id, run_id, principal, session
    )
    run = await session.get(Run, run_id)
    item = await session.get(WorkItem, UUID(result["workItem"]["id"]))
    assert run is not None and item is not None
    return {
        "data": {
            "run": _run_v2(await _run_public(session, run)),
            "workItem": await work_item_v2(session, item),
            "waitingForApproval": result["waitingForApproval"],
            "retryScheduled": False,
        }
    }


@v1_router.post("/organizations/{organization_id}/work-items/{work_item_id}/retry")
async def retry_item_v1(
    organization_id: UUID,
    work_item_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "jobs.run")
    item = await session.scalar(
        select(WorkItem).where(
            WorkItem.organization_id == organization_id,
            WorkItem.id == work_item_id,
        )
    )
    if item is None:
        raise not_found("Work item")
    if item.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "Only failed or cancelled work can be retried.")
    payload = dict(item.payload or {})
    payload["retryCount"] = int(payload.get("retryCount", 0)) + 1
    for key in ("lastError", "cancelledBy", "cancelledAt", "completedAt"):
        payload.pop(key, None)
    item.payload = payload
    item.status = "queued"
    item.scheduled_at = utcnow()
    await session.commit()
    await session.refresh(item)
    return {"workItem": await work_item_public(session, item)}


@v2_router.post("/organizations/{organization_id}/work-items/{work_item_id}/retry")
async def retry_item_v2(
    organization_id: UUID,
    work_item_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    await retry_item_v1(
        organization_id, work_item_id, principal, session
    )
    item = await session.get(WorkItem, work_item_id)
    assert item is not None
    return {"data": {"workItem": await work_item_v2(session, item)}}


async def _memory_state(
    session: AsyncSession, memory: Memory
) -> dict[str, Any]:
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.organization_id == memory.organization_id,
                    AuditEvent.event_type.in_(
                        ["memory.created", "memory.archived", "memory.promoted"]
                    ),
                )
                .order_by(AuditEvent.created_at)
            )
        ).all()
    )
    relevant = [
        event
        for event in events
        if str((event.resource or {}).get("id", "")) == str(memory.id)
    ]
    metadata: dict[str, Any] = {}
    for event in relevant:
        raw = (event.payload or {}).get("metadata", {})
        if isinstance(raw, dict):
            metadata.update(raw)
        if event.event_type == "memory.archived":
            metadata["status"] = "archived"
        if event.event_type == "memory.promoted":
            metadata["source"] = "promoted"
    return metadata


async def _memory_public(
    session: AsyncSession, memory: Memory
) -> dict[str, Any]:
    metadata = await _memory_state(session, memory)
    scope = memory.scope
    owner = memory.owner_id
    return {
        "id": str(memory.id),
        "organizationId": str(memory.organization_id),
        "scope": scope,
        "workerId": owner if scope == "worker" else None,
        "runId": owner if scope == "run" else None,
        "title": memory.title,
        "content": memory.content,
        "tags": list(metadata.get("tags", [])),
        "source": str(metadata.get("source", "human")),
        "sourceMemoryId": metadata.get("sourceMemoryId"),
        "status": str(metadata.get("status", "active")),
        "createdByType": str(metadata.get("createdByType", "human")),
        "createdBy": str(metadata.get("createdBy", "")),
        "correlationId": metadata.get("correlationId"),
        "createdAt": memory.created_at.isoformat(),
        "updatedAt": memory.updated_at.isoformat(),
        "expiresAt": memory.expires_at.isoformat() if memory.expires_at else None,
    }


def _memory_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": public["id"],
            "status": public["status"],
            "createdAt": public["createdAt"],
            "updatedAt": public["updatedAt"],
            "expiresAt": public["expiresAt"],
        },
        "organizationId": public["organizationId"],
        "scope": {
            "type": public["scope"],
            "workerId": public["workerId"],
            "runId": public["runId"],
        },
        "content": {
            "title": public["title"],
            "text": public["content"],
            "tags": public["tags"],
        },
        "provenance": {
            "source": public["source"],
            "sourceMemoryId": public["sourceMemoryId"],
            "createdByType": public["createdByType"],
            "createdBy": public["createdBy"],
            "correlationId": public["correlationId"],
        },
    }


def _default_memory_policy() -> dict[str, Any]:
    return {
        "runRetentionDays": 30,
        "workerRetentionDays": 180,
        "organizationRetentionDays": 365,
        "runtimeWriteMode": "auto_run_only",
        "longTermWriteMode": "human_only",
        "updatedBy": "",
        "updatedAt": None,
    }


async def _memory_policy(
    session: AsyncSession, organization_id: UUID
) -> dict[str, Any]:
    setting = await latest_setting(
        session, organization_id, "memory.policy.updated"
    )
    return {**_default_memory_policy(), **(setting or {})}


@v1_router.get("/organizations/{organization_id}/memory")
async def memory_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "memory.read")
    rows = list(
        (
            await session.scalars(
                select(Memory)
                .where(Memory.organization_id == organization_id)
                .order_by(desc(Memory.created_at))
                .limit(200)
            )
        ).all()
    )
    public = [await _memory_public(session, row) for row in rows]
    return {
        "memories": public,
        "policy": await _memory_policy(session, organization_id),
        "summary": {
            "total": len(public),
            "active": sum(item["status"] == "active" for item in public),
            "archived": sum(item["status"] == "archived" for item in public),
            "run": sum(item["scope"] == "run" for item in public),
            "worker": sum(item["scope"] == "worker" for item in public),
            "organization": sum(item["scope"] == "organization" for item in public),
        },
        "window": {"limit": 200, "truncated": len(rows) >= 200},
    }


@v2_router.get("/organizations/{organization_id}/memory")
async def memory_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await memory_v1(organization_id, principal, session)
    return {
        "data": {
            "memories": [_memory_v2(item) for item in result["memories"]],
            "policy": result["policy"],
            "summary": result["summary"],
            "window": result["window"],
        }
    }


@v1_router.post("/organizations/{organization_id}/memory", status_code=201)
async def create_memory_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "memory.manage")
    scope = str(payload.get("scope", "organization"))
    if scope not in {"worker", "organization"}:
        raise HTTPException(400, "Human memory may only target worker or organization scope.")
    owner_id = (
        str(payload.get("workerId", "")).strip()
        if scope == "worker"
        else str(organization_id)
    )
    if not owner_id:
        raise HTTPException(400, "workerId is required for worker memory.")
    policy = await _memory_policy(session, organization_id)
    days = (
        int(policy["workerRetentionDays"])
        if scope == "worker"
        else int(policy["organizationRetentionDays"])
    )
    memory = Memory(
        organization_id=organization_id,
        scope=scope,
        owner_id=owner_id,
        title=str(payload.get("title", "")).strip() or "Memory",
        content=str(payload.get("content", "")).strip(),
        expires_at=utcnow() + timedelta(days=days),
    )
    session.add(memory)
    await session.flush()
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="memory.created",
        category="system",
        resource_type="memory",
        resource_id=str(memory.id),
        resource_name=memory.title,
        outcome="active",
        summary=f"Memory {memory.title} created.",
        metadata={
            "tags": list(payload.get("tags", [])),
            "source": "human",
            "status": "active",
            "createdByType": "human",
            "createdBy": str(principal.user_id),
            "correlationId": None,
        },
    )
    await session.commit()
    await session.refresh(memory)
    return {"memory": await _memory_public(session, memory)}


@v2_router.post("/organizations/{organization_id}/memory", status_code=201)
async def create_memory_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await create_memory_v1(
        organization_id, payload, principal, session
    )
    return {"data": {"memory": _memory_v2(result["memory"])}}


async def _memory_transition(
    session: AsyncSession,
    organization_id: UUID,
    memory_id: UUID,
    principal: HumanPrincipal,
    event_type: str,
) -> Memory:
    require_permission(principal, "memory.manage")
    memory = await session.scalar(
        select(Memory).where(
            Memory.organization_id == organization_id,
            Memory.id == memory_id,
        )
    )
    if memory is None:
        raise not_found("Memory")
    if event_type == "memory.promoted":
        memory.scope = "organization"
        memory.owner_id = str(organization_id)
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type=event_type,
        category="system",
        resource_type="memory",
        resource_id=str(memory.id),
        resource_name=memory.title,
        outcome="promoted" if event_type == "memory.promoted" else "archived",
        summary=f"Memory {memory.title} {'promoted' if event_type == 'memory.promoted' else 'archived'}.",
        metadata={
            "source": "promoted" if event_type == "memory.promoted" else "human",
            "status": "active" if event_type == "memory.promoted" else "archived",
            "sourceMemoryId": str(memory_id) if event_type == "memory.promoted" else None,
        },
    )
    await session.commit()
    await session.refresh(memory)
    return memory


@v1_router.post("/organizations/{organization_id}/memory/{memory_id}/archive")
async def archive_memory_v1(
    organization_id: UUID,
    memory_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    memory = await _memory_transition(
        session, organization_id, memory_id, principal, "memory.archived"
    )
    return {"memory": await _memory_public(session, memory)}


@v2_router.post("/organizations/{organization_id}/memory/{memory_id}/archive")
async def archive_memory_v2(
    organization_id: UUID,
    memory_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await archive_memory_v1(
        organization_id, memory_id, principal, session
    )
    return {"data": {"memory": _memory_v2(result["memory"])}}


@v1_router.post("/organizations/{organization_id}/memory/{memory_id}/promote")
async def promote_memory_v1(
    organization_id: UUID,
    memory_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    memory = await _memory_transition(
        session, organization_id, memory_id, principal, "memory.promoted"
    )
    return {"memory": await _memory_public(session, memory)}


@v2_router.post("/organizations/{organization_id}/memory/{memory_id}/promote")
async def promote_memory_v2(
    organization_id: UUID,
    memory_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await promote_memory_v1(
        organization_id, memory_id, principal, session
    )
    return {"data": {"memory": _memory_v2(result["memory"])}}


@v1_router.put("/organizations/{organization_id}/memory/policy")
async def memory_policy_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "memory.manage")
    current = await _memory_policy(session, organization_id)
    updated = {
        **current,
        "runRetentionDays": int(payload.get("runRetentionDays", current["runRetentionDays"])),
        "workerRetentionDays": int(payload.get("workerRetentionDays", current["workerRetentionDays"])),
        "organizationRetentionDays": int(payload.get("organizationRetentionDays", current["organizationRetentionDays"])),
        "updatedBy": str(principal.user_id),
        "updatedAt": utcnow().isoformat(),
    }
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="memory.policy.updated",
        category="system",
        resource_type="organization",
        resource_id=str(organization_id),
        resource_name="Memory policy",
        outcome="updated",
        summary="Memory retention policy updated.",
        metadata=updated,
    )
    await session.commit()
    return {"policy": updated}


@v2_router.put("/organizations/{organization_id}/memory/policy")
async def memory_policy_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await memory_policy_v1(
            organization_id, payload, principal, session
        )
    }


def _artifact_public(artifact: Artifact) -> dict[str, Any]:
    meta = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
    return {
        "id": str(artifact.id),
        "organizationId": str(artifact.organization_id),
        "workerId": str(meta.get("workerId", "")),
        "runId": str(artifact.run_id) if artifact.run_id else "",
        "stepId": meta.get("stepId"),
        "name": str(meta.get("name", artifact.storage_key.rsplit("/", 1)[-1])),
        "kind": str(meta.get("kind", "run_result")),
        "contentType": artifact.media_type,
        "bytes": int(artifact.size_bytes or 0),
        "createdAt": artifact.created_at.isoformat(),
        "correlationId": str(meta.get("correlationId", "")),
    }


def _artifact_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": public["id"],
            "name": public["name"],
            "kind": public["kind"],
            "createdAt": public["createdAt"],
        },
        "organizationId": public["organizationId"],
        "assignment": {
            "workerId": public["workerId"],
            "runId": public["runId"],
            "stepId": public["stepId"],
        },
        "storage": {
            "contentType": public["contentType"],
            "bytes": public["bytes"],
        },
        "trace": {"correlationId": public["correlationId"]},
    }


@v1_router.get("/organizations/{organization_id}/artifacts")
async def artifacts_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "artifacts.read")
    rows = list(
        (
            await session.scalars(
                select(Artifact)
                .where(Artifact.organization_id == organization_id)
                .order_by(desc(Artifact.created_at))
                .limit(200)
            )
        ).all()
    )
    public = [_artifact_public(row) for row in rows]
    return {
        "artifacts": public,
        "summary": {"total": len(public)},
        "window": {"limit": 200, "truncated": len(rows) >= 200},
    }


@v2_router.get("/organizations/{organization_id}/artifacts")
async def artifacts_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await artifacts_v1(organization_id, principal, session)
    return {
        "data": {
            "items": [_artifact_v2(item) for item in result["artifacts"]],
            "summary": result["summary"],
            "window": result["window"],
        }
    }


@v1_router.post("/organizations/{organization_id}/artifacts/{artifact_id}/url")
async def artifact_url_v1(
    organization_id: UUID,
    artifact_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "artifacts.read")
    artifact = await session.scalar(
        select(Artifact).where(
            Artifact.organization_id == organization_id,
            Artifact.id == artifact_id,
        )
    )
    if artifact is None:
        raise not_found("Artifact")
    try:
        url = await object_storage_from_settings().signed_url(
            key=artifact.storage_key, expires_seconds=900
        )
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error
    return {"url": url}


@v2_router.post("/organizations/{organization_id}/artifacts/{artifact_id}/url")
async def artifact_url_v2(
    organization_id: UUID,
    artifact_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await artifact_url_v1(
            organization_id, artifact_id, principal, session
        )
    }




async def _run_execution_provenance(
    session: AsyncSession,
    run: Run,
) -> tuple[ExecutionProvenance, ...]:
    rows = list(
        (
            await session.scalars(
                select(Action)
                .where(
                    Action.organization_id == run.organization_id,
                    Action.run_id == run.id,
                )
                .order_by(Action.created_at)
            )
        ).all()
    )
    provenance: list[ExecutionProvenance] = []
    for action in rows:
        payload = action.payload if isinstance(action.payload, dict) else {}
        provenance.append(
            provenance_from_action_payload(
                scope=action.scope,
                resource_id=action.resource_id,
                payload=payload,
                action_id=str(action.id),
            )
        )

    steps = list(
        (
            await session.scalars(
                select(RunStep)
                .where(RunStep.run_id == run.id)
                .order_by(RunStep.step_index)
            )
        ).all()
    )
    known = {
        (item.capability, item.resource_id, item.action_id)
        for item in provenance
    }
    for step in steps:
        data = step.input if isinstance(step.input, dict) else {}
        scope = str(data.get("scope", "")).strip()
        resource_id = str(data.get("resourceId", "")).strip()
        if not scope or not resource_id:
            continue
        output = step.output if isinstance(step.output, dict) else {}
        action_id = str(output.get("actionId", "")).strip() or None
        key = (scope, resource_id, action_id)
        if key in known:
            continue
        provenance.append(
            ExecutionProvenance(
                provider=str(data.get("provider")) if data.get("provider") else None,
                capability=scope,
                resource_id=resource_id,
                adapter=str(data.get("adapter")) if data.get("adapter") else None,
                adapter_version=(
                    str(data.get("adapterVersion"))
                    if data.get("adapterVersion")
                    else None
                ),
                action_id=action_id,
            )
        )
        known.add(key)

    return tuple(provenance)

async def _result_detail(
    session: AsyncSession, result: Result
) -> dict[str, Any]:
    worker = await session.get(Worker, result.worker_id)
    job = await session.get(Job, result.job_id)
    version = await session.scalar(
        select(ResultVersion).where(
            ResultVersion.result_id == result.id,
            ResultVersion.version == result.latest_version,
        )
    )
    body = version.body if version and isinstance(version.body, dict) else {}
    return {
        "id": str(result.id),
        "organizationId": str(result.organization_id),
        "workerId": str(result.worker_id),
        "workerName": worker.name if worker else "",
        "department": worker.department if worker else "",
        "jobId": str(result.job_id),
        "jobName": job.name if job else "",
        "title": result.title,
        "summary": str(body.get("summary", "")),
        "status": result.status,
        "version": result.latest_version,
        "completedAt": str(body.get("completedAt") or result.updated_at.isoformat()),
        "createdAt": result.created_at.isoformat(),
        "hasTables": bool(body.get("hasTables", False)),
        "isNew": bool(body.get("isNew", True)),
        "workItemId": str(body.get("workItemId", "")),
        "runId": str(body.get("runId", "")),
        "agentId": str(body.get("agentId", "")),
        "contentType": "structured_v1",
        "blocks": list(body.get("blocks", [])),
        "artifactIds": list(body.get("artifactIds", [])),
        "sourceReferences": list(body.get("sourceReferences", [])),
        "capabilitiesUsed": list(body.get("capabilitiesUsed", [])),
        "actionIds": list(body.get("actionIds", [])),
        "approvalIds": list(body.get("approvalIds", [])),
        "executionProvenance": list(body.get("executionProvenance", [])),
        "correlationId": str(body.get("correlationId", "")),
        "runStartedAt": str(body.get("runStartedAt", "")),
        "durationMs": body.get("durationMs"),
    }


async def _results_workspace(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
) -> dict[str, Any]:
    require_permission(principal, "results.read")
    rows = list(
        (
            await session.scalars(
                select(Result)
                .where(Result.organization_id == organization_id)
                .order_by(desc(Result.updated_at))
                .limit(300)
            )
        ).all()
    )
    details = [await _result_detail(session, row) for row in rows]
    seen = await latest_setting(session, organization_id, "results.seen")
    seen_at = str(seen.get("seenAt")) if seen and seen.get("seenAt") else None
    for item in details:
        item["isNew"] = (
            True
            if seen_at is None
            else item["completedAt"] > seen_at
        )
    worker_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    job_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in details:
        worker_map[item["workerId"]].append(item)
        job_map[item["jobId"]].append(item)
    workers = [
        {
            "id": worker_id,
            "name": values[0]["workerName"],
            "department": values[0]["department"],
            "resultCount": len(values),
            "weekCount": len(values),
            "lastResultAt": max(v["completedAt"] for v in values),
        }
        for worker_id, values in worker_map.items()
    ]
    jobs = [
        {
            "id": job_id,
            "name": values[0]["jobName"],
            "workerId": values[0]["workerId"],
            "workerName": values[0]["workerName"],
            "department": values[0]["department"],
            "resultCount": len(values),
            "lastResultAt": max(v["completedAt"] for v in values),
            "latestResultId": values[0]["id"],
        }
        for job_id, values in job_map.items()
    ]
    exports = list(
        (
            await session.scalars(
                select(ResultExport)
                .where(ResultExport.organization_id == organization_id)
                .order_by(desc(ResultExport.created_at))
                .limit(25)
            )
        ).all()
    )
    recent_exports = []
    by_id = {UUID(item["id"]): item for item in details}
    for export in exports:
        detail = by_id.get(export.result_id)
        recent_exports.append(
            {
                "organizationId": str(organization_id),
                "resultId": str(export.result_id),
                "resultTitle": detail["title"] if detail else "",
                "format": export.format,
                "exportedBy": str(export.exported_by),
                "exportedAt": export.created_at.isoformat(),
            }
        )
    counts = Counter(item["status"] for item in details)
    return {
        "items": [
            {
                key: value
                for key, value in item.items()
                if key
                in {
                    "id",
                    "workerId",
                    "workerName",
                    "department",
                    "jobId",
                    "jobName",
                    "title",
                    "summary",
                    "status",
                    "version",
                    "completedAt",
                    "createdAt",
                    "hasTables",
                    "isNew",
                }
            }
            for item in details
        ],
        "workers": workers,
        "jobs": jobs,
        "recentExports": recent_exports,
        "summary": {
            "total": len(details),
            "new": sum(item["isNew"] for item in details),
            "completed": counts["completed"],
            "attention": counts["attention"],
            "failed": counts["failed"],
            "cancelled": counts["cancelled"],
        },
        "filters": {
            "departments": sorted(
                {item["department"] for item in details if item["department"]}
            ),
            "lastSeenAt": seen_at,
        },
        "window": {"limit": 300, "truncated": len(rows) >= 300},
    }


@v1_router.get("/organizations/{organization_id}/results")
async def results_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await _results_workspace(
        session, organization_id, principal
    )


@v2_router.get("/organizations/{organization_id}/results")
async def results_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await _results_workspace(
            session, organization_id, principal
        )
    }


@v1_router.get("/organizations/{organization_id}/results/{result_id}")
async def result_detail_v1(
    organization_id: UUID,
    result_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "results.read")
    result = await session.scalar(
        select(Result).where(
            Result.organization_id == organization_id,
            Result.id == result_id,
        )
    )
    if result is None:
        raise not_found("Result")
    return {"result": await _result_detail(session, result)}


@v2_router.get("/organizations/{organization_id}/results/{result_id}")
async def result_detail_v2(
    organization_id: UUID,
    result_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await result_detail_v1(
            organization_id, result_id, principal, session
        )
    }


@v1_router.post("/organizations/{organization_id}/results/seen")
async def results_seen_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "results.read")
    seen_at = utcnow().isoformat()
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="results.seen",
        category="system",
        resource_type="organization",
        resource_id=str(organization_id),
        resource_name="Results",
        outcome="seen",
        summary="Results marked as seen.",
        metadata={"seenAt": seen_at},
    )
    await session.commit()
    return {"seenAt": seen_at}


@v2_router.post("/organizations/{organization_id}/results/seen")
async def results_seen_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await results_seen_v1(
            organization_id, principal, session
        )
    }


@v1_router.post("/organizations/{organization_id}/results/backfill")
async def results_backfill_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "results.read")
    runs = list(
        (
            await session.scalars(
                select(Run)
                .where(
                    Run.organization_id == organization_id,
                    Run.status == "completed",
                )
                .order_by(desc(Run.created_at))
                .limit(100)
            )
        ).all()
    )
    created = 0
    for run in runs:
        item = await session.get(WorkItem, run.work_item_id)
        job = await session.get(Job, item.job_id) if item else None
        worker = await session.get(Worker, job.worker_id) if job else None
        if not item or not job or not worker:
            continue
        existing = await session.scalar(
            select(Result).where(
                Result.organization_id == organization_id,
                Result.job_id == job.id,
                Result.worker_id == worker.id,
            )
        )
        if existing is not None:
            continue
        execution_provenance = await _run_execution_provenance(session, run)
        provenance_rows = [item.as_dict() for item in execution_provenance]
        result = Result(
            organization_id=organization_id,
            worker_id=worker.id,
            job_id=job.id,
            latest_version=1,
            status="completed",
            title=f"{job.name} result",
        )
        session.add(result)
        await session.flush()
        session.add(
            ResultVersion(
                result_id=result.id,
                version=1,
                body={
                    "summary": run.result_summary or "Work completed.",
                    "completedAt": run.updated_at.isoformat(),
                    "workItemId": str(item.id),
                    "runId": str(run.id),
                    "agentId": str(worker.agent_identity_id),
                    "blocks": [
                        {
                            "type": "paragraph",
                            "text": run.result_summary or "Work completed.",
                        }
                    ],
                    "artifactIds": [],
                    "sourceReferences": [],
                    "capabilitiesUsed": sorted(
                        {item.capability for item in execution_provenance}
                    ),
                    "actionIds": [
                        item.action_id
                        for item in execution_provenance
                        if item.action_id is not None
                    ],
                    "approvalIds": [],
                    "executionProvenance": provenance_rows,
                    "correlationId": run.correlation_id,
                    "runStartedAt": run.created_at.isoformat(),
                    "durationMs": None,
                    "isNew": True,
                    "hasTables": False,
                },
                created_at=utcnow(),
            )
        )
        await TransactionalOutbox(session).enqueue(
            topic="result.created",
            aggregate_type="result",
            aggregate_id=str(result.id),
            payload={
                "organization_id": str(organization_id),
                "worker_id": str(worker.id),
                "job_id": str(job.id),
                "run_id": str(run.id),
                "result_id": str(result.id),
                "correlation_id": run.correlation_id,
            },
        )
        created += 1
    await session.commit()
    return {"created": created, "scanned": len(runs)}


@v2_router.post("/organizations/{organization_id}/results/backfill")
async def results_backfill_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await results_backfill_v1(
            organization_id, principal, session
        )
    }


@v1_router.post("/organizations/{organization_id}/results/{result_id}/exports", status_code=201)
async def result_export_v1(
    organization_id: UUID,
    result_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "results.export")
    result = await session.scalar(
        select(Result).where(
            Result.organization_id == organization_id,
            Result.id == result_id,
        )
    )
    if result is None:
        raise not_found("Result")
    export_format = str(payload.get("format", "md"))
    if export_format not in {
        "pdf",
        "docx",
        "md",
        "txt",
        "json",
        "csv",
        "xlsx",
        "print",
    }:
        raise HTTPException(400, "Unsupported result export format.")
    export = ResultExport(
        organization_id=organization_id,
        result_id=result.id,
        format=export_format,
        exported_by=principal.user_id,
    )
    session.add(export)
    await session.commit()
    await session.refresh(export)
    return {
        "organizationId": str(organization_id),
        "resultId": str(result.id),
        "resultTitle": result.title,
        "format": export.format,
        "exportedBy": str(principal.user_id),
        "exportedAt": export.created_at.isoformat(),
    }


@v2_router.post("/organizations/{organization_id}/results/{result_id}/exports", status_code=201)
async def result_export_v2(
    organization_id: UUID,
    result_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await result_export_v1(
            organization_id,
            result_id,
            payload,
            principal,
            session,
        )
    }
