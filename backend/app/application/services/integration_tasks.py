"""Asynchronous compilation into the existing managed runtime, one command once."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.application.services.integration_foundation import (
    IntegrationFoundation,
    IntegrationRequirementError,
    notify_integration_work,
)
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIProviderError
from app.domain.identity.permissions import permissions_for_role
from app.domain.integrations.foundation import IntegrationTaskDraft
from app.infrastructure.ai.provider import ai_gateway_from_settings
from app.infrastructure.database.models import (
    ConversationCommand,
    ConversationMessage,
    Job,
    JobRevision,
    OrganizationMembership,
    Worker,
    WorkItem,
)
from app.infrastructure.database.outbox import TransactionalOutbox


async def report_preparation_wait(session, command, *, status, message, category=None):
    receipt_status = "unavailable" if status in {"waiting_ai", "policy_denied"} else status
    command.status = status
    command.receipt = {"status": receipt_status, "message": message, "command_id": str(command.id),
                       "failure_category": category}
    notification = status + ":" + message
    if command.payload.get("lastRequirementMessage") == notification:
        return
    command.payload = {**command.payload, "lastRequirementMessage": notification}
    session.add(ConversationMessage(organization_id=command.organization_id, thread_id=command.thread_id,
        role="worker", content=message, command_references=[{"type": "command", "id": str(command.id),
            "family": "integration.execute", "status": status}]))
    await session.flush()
    await TransactionalOutbox(session).enqueue(topic="conversation.response.created", aggregate_type="conversation_command",
        aggregate_id=str(command.id), payload={"organization_id": str(command.organization_id), "thread_id": str(command.thread_id)})


async def prepare_integration_task(session, command_id: UUID) -> None:
    command = await session.scalar(select(ConversationCommand).where(ConversationCommand.id == command_id).with_for_update())
    if not command or command.target_id or command.status not in {"accepted", "waiting_integration", "clarification_required", "waiting_ai", "policy_denied"}:
        return
    task = (command.payload or {}).get("integrationTask", {})
    from app.application.services.preparation_ai import request_fingerprint
    initial_fingerprint = request_fingerprint(command)
    member = await session.scalar(select(OrganizationMembership).where(
        OrganizationMembership.organization_id == command.organization_id,
        OrganizationMembership.user_id == command.created_by))
    permissions = permissions_for_role(member.role) if member else frozenset()
    if not {"jobs.manage", "jobs.run"}.issubset(permissions):
        await report_preparation_wait(session, command, status="policy_denied", category="policy_denial",
            message="I cannot prepare this task because your permission to create and run jobs was removed. Restore access before retrying; nothing was executed.")
        return
    worker = await session.get(Worker, UUID(task["workerId"]))
    if not worker or worker.organization_id != command.organization_id:
        await report_preparation_wait(session, command, status="policy_denied", category="policy_denial",
            message="The requested worker is unavailable in this organization. Choose an accessible worker; nothing was executed.")
        return
    foundation = IntegrationFoundation(session)
    try:
        from app.application.services.preparation_ai import DurablePreparationGateway
        gateway = (DurablePreparationGateway(session, command) if settings.smart_planner_enabled
                   else ai_gateway_from_settings(session=session))
        if isinstance(gateway, DurablePreparationGateway) and not task.get("draft"):
            await gateway.prepare()
        draft = (IntegrationTaskDraft.model_validate(task["draft"]) if task.get("draft") else
                 await foundation.interpret(gateway,
                    organization_id=command.organization_id, user_id=command.created_by, instruction=task["instruction"], conversation_context=task.get("context", {})))
        task = {**task, "draft": draft.model_dump(mode="json")}
        # Inference releases the transaction. Cancellation, sharing and membership may
        # have changed while the model was responding; revalidate before creating grants.
        await session.refresh(command, with_for_update=True)
        if request_fingerprint(command) != initial_fingerprint:
            return
        if command.target_id or command.status not in {"accepted", "waiting_integration", "clarification_required", "waiting_ai", "policy_denied"}:
            return
        command.payload = {**command.payload, "integrationTask": task}
        member = await session.scalar(select(OrganizationMembership).where(
            OrganizationMembership.organization_id == command.organization_id,
            OrganizationMembership.user_id == command.created_by).execution_options(populate_existing=True))
        permissions = permissions_for_role(member.role) if member else frozenset()
        if not {"jobs.manage", "jobs.run"}.issubset(permissions):
            raise PermissionError("Task authority changed during preparation")
        bindings = await foundation.resolve(organization_id=command.organization_id, user_id=command.created_by, draft=draft, instruction=task["instruction"])
    except (IntegrationRequirementError, AIProviderError) as error:
        if isinstance(error, AIProviderError):
            await session.refresh(command, with_for_update=True)
            if request_fingerprint(command) != initial_fingerprint:
                return
            if error.category == "cancelled" and command.status not in {"accepted", "waiting_ai", "waiting_integration", "clarification_required", "policy_denied"}:
                return
            invalid_plan = error.category == "invalid_provider_response"
            await report_preparation_wait(session, command, status="waiting_ai", category=error.category,
                message=("AI task preparation returned tools or authority constraints outside the enabled catalog. Your request is saved; retry setup. Nothing was executed."
                    if invalid_plan else
                    "Task preparation reached its time limit. Your request is saved; no provider work was executed."
                    if error.category == "timeout" and not error.retryable else
                    "Task preparation is blocked by this workspace’s local AI budget. Your request is saved."
                    if error.category == "quota_exhausted" and error.organization_scoped else
                    "The eligible AI accounts could not admit this task because their usage allowance is exhausted. Your request is saved."
                    if error.category == "quota_exhausted" else
                    "AI task preparation is temporarily unavailable. Your request and context are saved; recovery will retry within its saved time budget."
                    if error.retryable else
                    "AI task preparation could not continue. Your request is saved; review the issue before retrying."))
        else:
            await report_preparation_wait(session, command,
                status="waiting_integration" if error.missing else "clarification_required", message=str(error))
        return
    except PermissionError:
        await report_preparation_wait(session, command, status="policy_denied", category="policy_denial",
            message="Account access changed during preparation. Review connection sharing and task permissions before retrying; nothing was executed.")
        return
    except ValidationError:
        await report_preparation_wait(session, command, status="waiting_ai", category="provider_model_outage",
            message="AI task preparation returned an invalid plan. Your request and context are saved; retry setup. No integration authority was granted.")
        return
    standing_excerpt = draft.standing_request_excerpt or ""
    standing = bool(standing_excerpt)
    if standing and standing_excerpt.casefold() not in task["instruction"].casefold():
        await report_preparation_wait(session, command, status="clarification_required",
            message="Please explicitly confirm the ongoing integration responsibility before it becomes permanent.")
        return
    if standing and not {"workforce.manage", "capabilities.manage", "policies.manage"}.issubset(permissions):
        await report_preparation_wait(session, command, status="policy_denied", category="policy_denial",
            message="Permanent integration responsibilities require permission to manage workers, capabilities and policies. This request has not granted standing authority.")
        return
    scopes = sorted({scope for binding in bindings for scope in binding["scopes"]})
    now = datetime.now(UTC)
    job = Job(id=uuid4(), organization_id=command.organization_id, worker_id=worker.id,
        name=draft.objective[:160], status="active", current_revision=1)
    revision = JobRevision(id=uuid4(), job_id=job.id, revision=1, created_by=command.created_by, created_at=now,
        definition={"objective": draft.objective, "instructions": task["instruction"],
            "completionCriteria": draft.completion_criteria, "requiredCapabilities": scopes,
            "priority": "normal", "autonomy": {"integrationFoundation": True,
                "sourceThreadId": task["threadId"], "sourceMessageId": task["messageId"],
                "integrationBindings": bindings, "oneShot": not standing, "resultsThreadId": task["threadId"]}})
    session.add(job)
    await session.flush()
    session.add(revision)
    await session.flush()
    item = WorkItem(id=uuid4(), organization_id=command.organization_id, job_id=job.id,
        job_revision_id=revision.id, status="queued", priority="normal", correlation_id=str(uuid4()),
        idempotency_key=f"integration-command:{command.id}", scheduled_at=now,
        payload={"requestedBy": str(command.created_by), "integrationOrigin": {
            "threadId": task["threadId"], "messageId": task["messageId"], "commandId": str(command.id)},
            "trigger": {"type": "manual"}})
    session.add(item)
    await session.flush()
    await foundation.bind(revision=revision, worker=worker, user_id=command.created_by,
        bindings=bindings, standing=standing, work_item_id=item.id)
    if standing:
        from app.infrastructure.database.models import WorkerDirective
        session.add(WorkerDirective(organization_id=command.organization_id, worker_id=worker.id,
            source_thread_id=command.thread_id, source_message_id=command.source_message_id,
            text=task["instruction"], status="active", created_by=command.created_by))
    command.target_type, command.target_id = "work_item", str(item.id)
    command.status = "accepted"
    command.receipt = {"status": "accepted", "message": "Your ongoing responsibility is saved and its first task is queued." if standing else "Your task is queued.",
        "command_id": str(command.id), "references": [{"type": "work_item", "id": str(item.id)}]}
    await notify_integration_work(session, item, key="queued", content="I resolved the account and resource. Your task is queued; I’ll report the verified outcome here.")
    await TransactionalOutbox(session).enqueue(topic="run.progress", aggregate_type="work_item", aggregate_id=str(item.id),
        payload={"organization_id": str(item.organization_id), "work_item_id": str(item.id),
            "current_step": 0, "continuation_phase": "plan", "correlation_id": item.correlation_id})
