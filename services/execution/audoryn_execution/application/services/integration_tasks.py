"""Asynchronous compilation into the existing managed runtime, one command once."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select

from audoryn_shared.application.services import entitlements
from audoryn_shared.application.services.grounding import grounded_in
from audoryn_shared.application.services.integration_foundation import (
    IntegrationFoundation,
    IntegrationRequirementError,
    notify_integration_work,
    provider_label,
)
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.domain.ai.providers import AIProviderError
from audoryn_shared.domain.conversation.intent import PENDING_COMMAND_STATUSES
from audoryn_shared.domain.identity.permissions import permissions_for_role
from audoryn_shared.domain.integrations.foundation import IntegrationTaskDraft
from audoryn_shared.execution.bootstrap import execution_provider_registry
from audoryn_shared.infrastructure.ai.provider import ai_gateway_from_settings
from audoryn_shared.infrastructure.database.models import (
    ConversationCommand,
    ConversationMessage,
    Job,
    JobRevision,
    OrganizationMembership,
    Worker,
    WorkItem,
)
from audoryn_shared.infrastructure.database.outbox import TransactionalOutbox


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


# GitHub's names for its "Subscribe to events" settings.
_EVENT_SETTING = {"issues": "Issues", "issue_comment": "Issue comment", "pull_request": "Pull request",
                  "pull_request_review": "Pull request review", "push": "Push", "release": "Release",
                  "workflow_run": "Workflow run", "check_run": "Check run"}


async def _set_up_event_trigger(session, command, task, draft, worker, job, revision, bindings, foundation) -> None:
    """A task that runs each time an event happens: a standing job, its grants and one event
    subscription per watched resource, with results posted to this conversation. Nothing runs now."""
    from audoryn_shared.infrastructure.database.models import (
        IntegrationEventSubscription,
        WorkerDirective,
    )
    trigger = draft.event_trigger
    revision.definition = {**revision.definition, "autonomy": {**revision.definition["autonomy"],
        "eventTrigger": trigger.model_dump(mode="json")}}
    await foundation.bind(revision=revision, worker=worker, user_id=command.created_by, bindings=bindings, standing=True)
    watched = [binding for binding in bindings if binding["provider"] == trigger.provider]
    for binding in watched:
        session.add(IntegrationEventSubscription(organization_id=command.organization_id, job_revision_id=revision.id,
            resource_id=UUID(binding["id"]), event_type=trigger.event, results_thread_id=command.thread_id, enabled=True))
    session.add(WorkerDirective(organization_id=command.organization_id, worker_id=worker.id,
        source_thread_id=command.thread_id, source_message_id=command.source_message_id,
        text=task["instruction"], status="active", created_by=command.created_by))
    words = getattr(execution_provider_registry().get(trigger.provider), "event_triggers", {}).get(trigger.event, trigger.event)
    names = ", ".join(binding["name"] for binding in watched)
    # The objective is quoted, not spliced into a sentence: it reads right however it was worded.
    action = draft.objective.strip()[:300]
    message = (f"Set up: every time {words} in {names}, I'll run this task: “{action}” Results will be posted "
               "here; you can pause or stop it from the job.")
    command.target_type, command.target_id = "job", str(job.id)
    command.status = "completed"
    command.receipt = {"status": "completed", "message": message, "command_id": str(command.id),
                       "references": [{"type": "job", "id": str(job.id)}]}
    session.add(ConversationMessage(organization_id=command.organization_id, thread_id=command.thread_id,
        role="worker", content=message, command_references=[{"type": "command", "id": str(command.id),
            "family": "integration.execute", "status": "completed"}]))
    await session.flush()
    await TransactionalOutbox(session).enqueue(topic="conversation.response.created", aggregate_type="conversation_command",
        aggregate_id=str(command.id), payload={"organization_id": str(command.organization_id), "thread_id": str(command.thread_id)})


JOB_PERMISSIONS = frozenset({"jobs.manage", "jobs.run"})
STANDING_PERMISSIONS = frozenset({"workforce.manage", "capabilities.manage", "policies.manage"})


async def _permissions(session, command, *, fresh: bool = False) -> frozenset[str]:
    """The requester's current permissions (re-read from the database when `fresh`)."""
    query = select(OrganizationMembership).where(
        OrganizationMembership.organization_id == command.organization_id,
        OrganizationMembership.user_id == command.created_by)
    member = await session.scalar(query.execution_options(populate_existing=True) if fresh else query)
    return permissions_for_role(member.role) if member else frozenset()


def _still_pending(command) -> bool:
    return not command.target_id and command.status in {"accepted", *PENDING_COMMAND_STATUSES}


def _ai_failure_message(error: AIProviderError) -> str:
    if error.category == "invalid_provider_response":
        return "AI task preparation returned tools or authority constraints outside the enabled catalog. Your request is saved; retry setup. Nothing was executed."
    if error.category == "timeout" and not error.retryable:
        return "Task preparation reached its time limit. Your request is saved; no provider work was executed."
    if error.category == "quota_exhausted":
        return ("Task preparation is blocked by this workspace’s local AI budget. Your request is saved."
                if error.organization_scoped else
                "The eligible AI accounts could not admit this task because their usage allowance is exhausted. Your request is saved.")
    if error.retryable:
        return "AI task preparation is temporarily unavailable. Your request and context are saved; recovery will retry within its saved time budget."
    return "AI task preparation could not continue. Your request is saved; review the issue before retrying."


async def prepare_integration_task(session, command_id: UUID) -> None:
    """Turn a saved request into a bound, queued task (or an event job), or say what it waits for."""
    command = await session.scalar(select(ConversationCommand).where(ConversationCommand.id == command_id).with_for_update())
    if not command or not _still_pending(command):
        return
    task = (command.payload or {}).get("integrationTask", {})
    if not JOB_PERMISSIONS <= await _permissions(session, command):
        await report_preparation_wait(session, command, status="policy_denied", category="policy_denial",
            message="I cannot prepare this task because your permission to create and run jobs was removed. Restore access before retrying; nothing was executed.")
        return
    worker = await session.get(Worker, UUID(task["workerId"]))
    if not worker or worker.organization_id != command.organization_id:
        await report_preparation_wait(session, command, status="policy_denied", category="policy_denial",
            message="The requested worker is unavailable in this organization. Choose an accessible worker; nothing was executed.")
        return
    foundation = IntegrationFoundation(session)
    prepared = await _draft_and_bind(session, command, task, foundation)
    if prepared is None:
        return
    task, draft, bindings, permissions = prepared
    trigger = draft.event_trigger
    # Ongoing authority only from the person's own words; otherwise the task is one-off. An event
    # trigger is ongoing by nature, and its words were checked when it was drafted.
    standing = (bool(draft.standing_request_excerpt) and grounded_in(draft.standing_request_excerpt, task["instruction"])) \
        or trigger is not None
    blocker = await _event_blocker(trigger) if trigger is not None else None
    if blocker:
        await report_preparation_wait(session, command, status="waiting_integration", message=blocker)
        return
    if standing and not STANDING_PERMISSIONS <= permissions:
        await report_preparation_wait(session, command, status="policy_denied", category="policy_denial",
            message="Permanent integration responsibilities require permission to manage workers, capabilities and policies. This request has not granted standing authority.")
        return
    if standing and (blocker := await _plan_blocker(session, command.organization_id, worker.id, trigger)):
        await report_preparation_wait(session, command, status="policy_denied", category="policy_denial", message=blocker)
        return
    job, revision = await _create_job(session, command, task, draft, worker, bindings, standing)
    if trigger is not None:
        await _set_up_event_trigger(session, command, task, draft, worker, job, revision, bindings, foundation)
        return
    await _queue_task(session, command, task, worker, job, revision, bindings, standing, foundation)


async def _draft_and_bind(session, command, task, foundation):
    """The task's draft (from the model, or saved) and its resource bindings, or None after
    reporting why preparation waits. The request is revalidated after the model answers."""
    from audoryn_shared.application.services.preparation_ai import (
        DurablePreparationGateway,
        request_fingerprint,
    )

    initial_fingerprint = request_fingerprint(command)
    try:
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
        if request_fingerprint(command) != initial_fingerprint or not _still_pending(command):
            return None
        command.payload = {**command.payload, "integrationTask": task}
        permissions = await _permissions(session, command, fresh=True)
        if not JOB_PERMISSIONS <= permissions:
            raise PermissionError("Task authority changed during preparation")
        bindings = await foundation.resolve(organization_id=command.organization_id, user_id=command.created_by, draft=draft, instruction=task["instruction"])
    except AIProviderError as error:
        await session.refresh(command, with_for_update=True)
        if request_fingerprint(command) == initial_fingerprint and (error.category != "cancelled" or _still_pending(command)):
            await report_preparation_wait(session, command, status="waiting_ai", category=error.category,
                                          message=_ai_failure_message(error))
        return None
    except IntegrationRequirementError as error:
        await report_preparation_wait(session, command,
            status="waiting_integration" if error.missing else "clarification_required", message=str(error))
        return None
    except PermissionError:
        await report_preparation_wait(session, command, status="policy_denied", category="policy_denial",
            message="Account access changed during preparation. Review connection sharing and task permissions before retrying; nothing was executed.")
        return None
    except ValidationError:
        await report_preparation_wait(session, command, status="waiting_ai", category="provider_model_outage",
            message="AI task preparation returned an invalid plan. Your request and context are saved; retry setup. No integration authority was granted.")
        return None
    return task, draft, bindings, permissions


async def _event_blocker(trigger) -> str | None:
    """Why the account cannot deliver the trigger's event yet, or None."""
    provider = execution_provider_registry().get(trigger.provider)
    events_enabled = getattr(provider, "events_enabled", None)
    if not callable(events_enabled) or not events_enabled():
        return (f"I can't react to {provider_label(trigger.provider)} events yet: event delivery isn't turned on "
                "for this workspace's server. An administrator needs to enable it; nothing was set up.")
    kind = trigger.event.split(".")[0]
    events = await cast(Any, provider).subscribed_events() if hasattr(provider, "subscribed_events") else None
    if events is not None and kind not in events:
        setting = _EVENT_SETTING.get(kind, kind.replace("_", " ").capitalize())
        return (f"{provider_label(trigger.provider)} isn't sending “{setting}” events to Audoryn yet, so I "
                f"can't react to them. In the GitHub App's settings, under Permissions & events, tick "
                f"“{setting}” in Subscribe to events, then ask me again. Nothing was set up.")
    return None


async def _plan_blocker(session, organization_id, worker_id, trigger) -> str | None:
    """Why the organisation's plan does not allow this standing job, or None."""
    try:
        if trigger is not None:
            await entitlements.require_feature(session, organization_id, "events.enabled")
        await entitlements.require_room(session, organization_id, "jobs.per_worker.max", worker_id=worker_id)
    except entitlements.EntitlementDenied as denied:
        return denied.message
    return None


async def _create_job(session, command, task, draft, worker, bindings, standing):
    scopes = sorted({scope for binding in bindings for scope in binding["scopes"]})
    job = Job(id=uuid4(), organization_id=command.organization_id, worker_id=worker.id,
        name=draft.objective[:160], status="active", current_revision=1)
    revision = JobRevision(id=uuid4(), job_id=job.id, revision=1, created_by=command.created_by, created_at=datetime.now(UTC),
        definition={"objective": draft.objective, "instructions": task["instruction"],
            "completionCriteria": draft.completion_criteria, "requiredCapabilities": scopes,
            "priority": "normal", "autonomy": {"integrationFoundation": True,
                "sourceThreadId": task["threadId"], "sourceMessageId": task["messageId"],
                "integrationBindings": bindings, "oneShot": not standing, "resultsThreadId": task["threadId"]}})
    session.add(job)
    await session.flush()
    session.add(revision)
    await session.flush()
    return job, revision


async def _queue_task(session, command, task, worker, job, revision, bindings, standing, foundation) -> None:
    """The job's first task, bound and queued, and the command pointed at it."""
    item = WorkItem(id=uuid4(), organization_id=command.organization_id, job_id=job.id,
        job_revision_id=revision.id, status="queued", priority="normal", correlation_id=str(uuid4()),
        idempotency_key=f"integration-command:{command.id}", scheduled_at=revision.created_at,
        payload={"requestedBy": str(command.created_by), "integrationOrigin": {
            "threadId": task["threadId"], "messageId": task["messageId"], "commandId": str(command.id)},
            "trigger": {"type": "manual"}})
    session.add(item)
    await session.flush()
    await foundation.bind(revision=revision, worker=worker, user_id=command.created_by,
        bindings=bindings, standing=standing, work_item_id=item.id)
    if standing:
        from audoryn_shared.infrastructure.database.models import WorkerDirective
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
