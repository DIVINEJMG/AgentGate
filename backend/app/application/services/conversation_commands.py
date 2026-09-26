from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.governance_routes import (
    _create_policy,
    _update_policy,
    policy_status_v1,
)
from app.api.jobs_routes import (
    _provision_managed_job_authority,
    cancel_item,
    create_job,
    current_revision,
    job_status_v1,
    queue_job,
    save_trigger_config,
    update_job,
)
from app.api.product_common import require_permission
from app.api.runtime_result_routes import retry_item_v1
from app.api.workforce_routes import (
    delete_worker_v1,
    set_worker_status,
    update_worker,
)
from app.application.services.attachment_ingestion import AttachmentIngestionService
from app.application.services.browser_origin_authority import (
    ensure_managed_browser_origins,
    explicit_http_origins,
)
from app.application.services.capability_autoresolver import SemanticCapabilityResolver
from app.application.services.worker_autonomy import WorkerAutonomyService
from app.application.services.worker_memory import (
    WorkerMemoryService,
    contains_secret_material,
)
from app.domain.ai.providers import AIGateway, AIInvocationContext
from app.domain.conversation.intent import (
    CommandReceipt,
    CommandReference,
    EntityReference,
    WorkerCommandIntent,
)
from app.domain.identity.principals import HumanPrincipal
from app.domain.workforce.drafts import CapabilityNeed
from app.infrastructure.database.models import (
    ConversationCommand,
    ConversationMessage,
    ConversationThread,
    Integration,
    Job,
    Policy,
    Result,
    ResultVersion,
    Run,
    RunStep,
    Worker,
    WorkerDirective,
    WorkItem,
)
from app.infrastructure.database.outbox import TransactionalOutbox


class CommandResolutionError(ValueError):
    pass


class CrossTenantReferenceError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class CompiledCommandOutcome:
    command: ConversationCommand
    receipt: CommandReceipt


class ConversationCommandCompiler:
    """Compile typed AI intent into canonical Aduoryn operations.

    The model output is never executed directly. Every target is resolved again from
    canonical state inside the authenticated organization before any service is called.
    """

    def __init__(
        self,
        session: AsyncSession,
        gateway: AIGateway,
    ) -> None:
        self._session = session
        self._gateway = gateway

    async def compile_and_execute(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        thread: ConversationThread,
        source_message: ConversationMessage,
        intent: WorkerCommandIntent,
        authoritative_context: dict[str, Any],
        confirmed: bool = False,
        existing_command: ConversationCommand | None = None,
    ) -> CompiledCommandOutcome:
        if principal.organization_id != organization_id:
            raise CrossTenantReferenceError("Cross-organization command denied.")
        if thread.organization_id != organization_id:
            raise CrossTenantReferenceError("Cross-organization thread denied.")

        command = existing_command or ConversationCommand(
            organization_id=organization_id,
            thread_id=thread.id,
            source_message_id=source_message.id,
            family=intent.family,
            status="accepted",
            payload={"intent": intent.model_dump(mode="json")},
            receipt={},
            created_by=principal.user_id,
            requires_confirmation=False,
        )
        if existing_command is None:
            self._session.add(command)
            await self._session.flush()
            await self._event(
                "conversation.command.accepted",
                command,
                thread=thread,
                worker_id=thread.worker_id,
                payload={"family": intent.family, "status": "accepted"},
            )

        try:
            receipt = await self._dispatch(
                organization_id=organization_id,
                principal=principal,
                thread=thread,
                source_message=source_message,
                intent=intent,
                context=authoritative_context,
                command=command,
                confirmed=confirmed,
            )
        except CommandResolutionError as exc:
            receipt = CommandReceipt(
                status="clarification_required",
                message=str(exc),
                command_id=command.id,
            )
        except CrossTenantReferenceError:
            command.status = "rejected"
            command.receipt = {
                "status": "rejected",
                "message": "That reference is outside this organization.",
            }
            await self._session.commit()
            raise

        if receipt.command_id is None:
            receipt.command_id = command.id
        command.status = receipt.status
        command.receipt = receipt.model_dump(mode="json")
        command.requires_confirmation = receipt.status == "waiting_confirmation"
        await self._event(
            (
                "conversation.clarification_required"
                if receipt.status == "clarification_required"
                else "conversation.approval_required"
                if receipt.status == "waiting_confirmation"
                else "conversation.integration_required"
                if receipt.status == "waiting_integration"
                else "conversation.command.accepted"
                if receipt.status == "accepted"
                else "conversation.command.completed"
            ),
            command,
            thread=thread,
            worker_id=thread.worker_id,
            payload={
                "family": command.family,
                "status": receipt.status,
                "message": receipt.message,
                "references": [
                    reference.model_dump(mode="json") for reference in receipt.references
                ],
            },
        )
        await self._session.commit()
        await self._session.refresh(command)
        return CompiledCommandOutcome(command=command, receipt=receipt)

    async def confirm(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        command: ConversationCommand,
        thread: ConversationThread,
        source_message: ConversationMessage,
        authoritative_context: dict[str, Any],
    ) -> CompiledCommandOutcome:
        if command.organization_id != organization_id:
            raise CrossTenantReferenceError("Cross-organization command denied.")
        if (
            command.created_by != principal.user_id
            and "workforce.manage" not in principal.permissions
        ):
            raise PermissionError("Only the command creator or a workforce manager may confirm it.")
        if command.status != "waiting_confirmation":
            raise CommandResolutionError("This command is not waiting for confirmation.")
        raw = command.payload.get("intent") if isinstance(command.payload, dict) else None
        if not isinstance(raw, dict):
            raise CommandResolutionError("Stored command intent is unavailable.")
        intent = WorkerCommandIntent.model_validate(raw)
        return await self.compile_and_execute(
            organization_id=organization_id,
            principal=principal,
            thread=thread,
            source_message=source_message,
            intent=intent,
            authoritative_context=authoritative_context,
            confirmed=True,
            existing_command=command,
        )

    async def _dispatch(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        thread: ConversationThread,
        source_message: ConversationMessage,
        intent: WorkerCommandIntent,
        context: dict[str, Any],
        command: ConversationCommand,
        confirmed: bool,
    ) -> CommandReceipt:
        family = intent.family

        if family == "worker.create":
            result = await WorkerAutonomyService(
                self._session,
                self._gateway,
            ).create_from_instruction(
                organization_id=organization_id,
                principal=principal,
                instruction=source_message.content,
                authoritative_context=context,
                source_thread_id=thread.id,
                source_message_id=source_message.id,
            )
            command.target_type = "worker"
            command.target_id = str(result.worker.id)
            if result.missing_integrations:
                providers = ", ".join(result.missing_integrations)
                return CommandReceipt(
                    status="waiting_integration",
                    message=(
                        f"I created {result.worker.name} and its job setup. "
                        f"Before all work can start, connect: {providers}."
                    ),
                    references=list(result.references),
                    command_id=command.id,
                    failure_category="missing_integration",
                    action_hints=[
                        {
                            "kind": "connect_integration",
                            "provider": provider,
                            "label": f"Connect {provider.replace('_', ' ').title()}",
                        }
                        for provider in result.missing_integrations
                    ],
                )
            job_names = ", ".join(job.name for job in result.jobs)
            return CommandReceipt(
                status="completed",
                message=(
                    f"I created {result.worker.name}"
                    + (f" with {job_names}." if job_names else ".")
                ),
                references=list(result.references),
                command_id=command.id,
            )

        if family in {
            "worker.update",
            "worker.status",
            "worker.pause",
            "worker.resume",
            "worker.delete",
            "instruction.add",
            "instruction.remove",
        }:
            worker = await self._resolve_worker(organization_id, intent.worker, thread)
            command.target_type = "worker"
            command.target_id = str(worker.id)

            if family == "worker.status":
                require_permission(principal, "workforce.read")
                return await self._worker_status(organization_id, worker, command)
            if family == "worker.pause":
                updated = await set_worker_status(
                    self._session, organization_id, worker.id, principal, "paused"
                )
                await self._worker_event(updated, "paused")
                return self._receipt(
                    "completed",
                    f"I paused {updated.name}. New runtime work will not proceed while the worker is paused.",
                    updated,
                )
            if family == "worker.resume":
                updated = await set_worker_status(
                    self._session, organization_id, worker.id, principal, "active"
                )
                await self._worker_event(updated, "active")
                paused_jobs = list(
                    (
                        await self._session.scalars(
                            select(Job).where(
                                Job.organization_id == organization_id,
                                Job.worker_id == worker.id,
                                Job.status == "paused",
                            )
                        )
                    ).all()
                )
                resumed_job = None
                if intent.job is not None:
                    resumed_job = await self._resolve_job(
                        organization_id,
                        intent.job,
                        worker,
                        prefer_current=False,
                    )
                    if resumed_job.status == "paused":
                        resumed_job.status = "active"
                        await self._resume_job_schedule(organization_id, resumed_job, principal)
                elif len(paused_jobs) == 1:
                    resumed_job = paused_jobs[0]
                    resumed_job.status = "active"
                    await self._resume_job_schedule(organization_id, resumed_job, principal)
                await self._session.commit()
                message = f"I resumed {updated.name}."
                references = [self._ref("worker", updated.id, updated.name)]
                if resumed_job is not None:
                    message += f" I also resumed {resumed_job.name}."
                    references.append(self._ref("job", resumed_job.id, resumed_job.name))
                return CommandReceipt(
                    status="completed",
                    message=message,
                    references=references,
                    command_id=command.id,
                )
            if family == "worker.update":
                updated = await update_worker(
                    self._session,
                    organization_id,
                    worker.id,
                    principal,
                    dict(intent.arguments),
                )
                return self._receipt(
                    "completed",
                    f"I updated {updated.name}.",
                    updated,
                )
            if family == "worker.delete":
                require_permission(principal, "workforce.manage")
                if not confirmed:
                    return CommandReceipt(
                        status="waiting_confirmation",
                        message=(
                            f"Deleting {worker.name} is destructive. Confirm this command "
                            "after its jobs have been removed."
                        ),
                        references=[self._ref("worker", worker.id, worker.name)],
                        command_id=command.id,
                        failure_category="approval_wait",
                        action_hints=[
                            {
                                "kind": "confirm_command",
                                "label": "Confirm deletion",
                            }
                        ],
                    )
                jobs = list(
                    (
                        await self._session.scalars(
                            select(Job).where(
                                Job.organization_id == organization_id,
                                Job.worker_id == worker.id,
                            )
                        )
                    ).all()
                )
                if jobs:
                    raise CommandResolutionError(
                        f"{worker.name} still has {len(jobs)} job(s). Delete those jobs before deleting the worker."
                    )
                worker_id = worker.id
                worker_name = worker.name
                await delete_worker_v1(
                    organization_id,
                    worker_id,
                    principal,
                    self._session,
                )
                return CommandReceipt(
                    status="completed",
                    message=f"I deleted {worker_name}.",
                    references=[self._ref("worker", worker_id, worker_name)],
                    command_id=command.id,
                )
            if family == "instruction.add":
                require_permission(principal, "workforce.manage")
                text = str(
                    intent.arguments.get("instruction")
                    or intent.arguments.get("text")
                    or source_message.content
                ).strip()
                if not text:
                    raise CommandResolutionError("What instruction should I add?")
                if contains_secret_material(text):
                    raise CommandResolutionError(
                        "Secrets and credentials cannot be stored as Worker instructions."
                    )
                directive = WorkerDirective(
                    organization_id=organization_id,
                    worker_id=worker.id,
                    text=text,
                    status="active",
                    created_by=principal.user_id,
                    source_thread_id=thread.id,
                    source_message_id=source_message.id,
                )
                self._session.add(directive)
                await self._session.flush()
                await WorkerMemoryService(self._session).add_standing_memory(
                    worker=worker,
                    instruction=text,
                    source_thread_id=thread.id,
                    source_message_id=source_message.id,
                )
                return CommandReceipt(
                    status="completed",
                    message=f"I added that as a standing instruction for {worker.name}.",
                    references=[
                        self._ref("worker", worker.id, worker.name),
                        self._ref("directive", directive.id, None),
                    ],
                    command_id=command.id,
                )
            if family == "instruction.remove":
                require_permission(principal, "workforce.manage")
                directive = await self._resolve_directive(organization_id, worker, intent.arguments)
                directive.status = "removed"
                return CommandReceipt(
                    status="completed",
                    message=f"I removed that standing instruction from {worker.name}.",
                    references=[
                        self._ref("worker", worker.id, worker.name),
                        self._ref("directive", directive.id, None),
                    ],
                    command_id=command.id,
                )

        if family in {
            "job.create",
            "job.update",
            "job.stop",
            "job.retry",
            "job.status",
            "work.execute_now",
            "schedule.create",
            "schedule.update",
            "schedule.pause",
            "schedule.resume",
        }:
            worker = await self._resolve_worker_for_job(organization_id, intent.worker, thread)
            if family == "job.create":
                require_permission(principal, "jobs.manage")
                if worker is None:
                    raise CommandResolutionError("Which worker should own this job?")
                name = str(
                    intent.arguments.get("name")
                    or (intent.job.name if intent.job is not None else "")
                ).strip()
                if not name:
                    raise CommandResolutionError("What should I call this job?")
                job_payload, browser_scopes = await self._prepare_existing_worker_job_payload(
                    organization_id=organization_id,
                    principal=principal,
                    worker=worker,
                    source_message=source_message,
                    source_thread_id=thread.id,
                    name=name,
                    arguments=dict(intent.arguments),
                )
                job = await create_job(
                    self._session,
                    organization_id,
                    principal,
                    job_payload,
                )
                if browser_scopes:
                    await _provision_managed_job_authority(
                        self._session,
                        organization_id,
                        worker,
                        principal,
                        list(browser_scopes),
                    )
                await job_status_v1(
                    organization_id,
                    job.id,
                    {"status": "active"},
                    principal,
                    self._session,
                )
                command.target_type = "job"
                command.target_id = str(job.id)
                return CommandReceipt(
                    status="completed",
                    message=f"I created the {job.name} job for {worker.name}.",
                    references=[
                        self._ref("worker", worker.id, worker.name),
                        self._ref("job", job.id, job.name),
                    ],
                    command_id=command.id,
                )

            job = await self._resolve_job(
                organization_id,
                intent.job,
                worker,
                prefer_current=True,
            )
            command.target_type = "job"
            command.target_id = str(job.id)

            if family == "job.status":
                require_permission(principal, "jobs.read")
                return await self._job_status(organization_id, job, command)
            if family == "job.update":
                updated = await update_job(
                    self._session,
                    organization_id,
                    job.id,
                    principal,
                    dict(intent.arguments),
                )
                return self._receipt(
                    "completed",
                    f"I updated {updated.name}.",
                    updated,
                    object_type="job",
                )
            if family == "job.stop":
                require_permission(principal, "jobs.manage")
                await job_status_v1(
                    organization_id,
                    job.id,
                    {"status": "paused"},
                    principal,
                    self._session,
                )
                await self._pause_job_schedule(organization_id, job, principal)
                await self._cancel_open_work(organization_id, job, principal)
                return self._receipt(
                    "completed",
                    f"I stopped {job.name} and cancelled its open work.",
                    job,
                    object_type="job",
                )
            if family == "job.retry":
                require_permission(principal, "jobs.run")
                item = await self._latest_work_item(
                    organization_id, job, statuses={"failed", "cancelled"}
                )
                if item is None:
                    raise CommandResolutionError(
                        f"{job.name} has no failed or cancelled work item to retry."
                    )
                await retry_item_v1(organization_id, item.id, principal, self._session)
                return CommandReceipt(
                    status="accepted",
                    message=f"I queued a retry for {job.name}.",
                    references=[
                        self._ref("job", job.id, job.name),
                        self._ref("work_item", item.id, None),
                    ],
                    command_id=command.id,
                )
            if family == "work.execute_now":
                item = await queue_job(self._session, organization_id, job, principal)
                return CommandReceipt(
                    status="accepted",
                    message=f"I queued {job.name} to run now.",
                    references=[
                        self._ref("job", job.id, job.name),
                        self._ref("work_item", item.id, None),
                    ],
                    command_id=command.id,
                )
            if family.startswith("schedule."):
                config = await self._change_schedule(
                    organization_id=organization_id,
                    principal=principal,
                    job=job,
                    family=family,
                    arguments=intent.arguments,
                )
                schedule = config.get("schedule", {})
                if not isinstance(schedule, dict):
                    schedule = {}
                state = "active" if schedule.get("enabled") else "paused"
                return CommandReceipt(
                    status="completed",
                    message=f"I changed {job.name}'s schedule. It is now {state}.",
                    references=[
                        self._ref("job", job.id, job.name),
                        CommandReference(
                            type="schedule",
                            id=str(config.get("id") or ""),
                            name=job.name,
                        ),
                    ],
                    command_id=command.id,
                )

        if family in {"policy.add", "policy.update", "policy.remove"}:
            if family == "policy.add":
                require_permission(principal, "policies.manage")
                worker = await self._resolve_worker_for_job(organization_id, intent.worker, thread)
                payload = self._policy_payload(intent, worker)
                policy = await _create_policy(self._session, organization_id, principal, payload)
                command.target_type = "policy"
                command.target_id = str(policy.id)
                return self._receipt(
                    "completed",
                    f"I added the {policy.name} policy.",
                    policy,
                    object_type="policy",
                )

            policy = await self._resolve_policy(organization_id, intent.policy)
            command.target_type = "policy"
            command.target_id = str(policy.id)
            if family == "policy.update":
                updated = await _update_policy(
                    self._session,
                    organization_id,
                    policy.id,
                    principal,
                    dict(intent.arguments),
                )
                return self._receipt(
                    "completed",
                    f"I updated the {updated.name} policy.",
                    updated,
                    object_type="policy",
                )
            require_permission(principal, "policies.manage")
            if not confirmed:
                return CommandReceipt(
                    status="waiting_confirmation",
                    message=f"Removing {policy.name} disables a governance rule. Confirm this command to continue.",
                    references=[self._ref("policy", policy.id, policy.name)],
                    command_id=command.id,
                    failure_category="approval_wait",
                    action_hints=[
                        {
                            "kind": "confirm_command",
                            "label": "Confirm policy removal",
                        }
                    ],
                )
            await policy_status_v1(
                organization_id,
                policy.id,
                {"status": "disabled"},
                principal,
                self._session,
            )
            return self._receipt(
                "completed",
                f"I disabled the {policy.name} policy.",
                policy,
                object_type="policy",
            )

        if family == "result.query":
            require_permission(principal, "results.read")
            worker = await self._resolve_worker_for_job(organization_id, intent.worker, thread)
            return await self._result_query(organization_id, worker, intent, command)

        if family == "failure.explain":
            require_permission(principal, "jobs.read")
            worker = await self._resolve_worker_for_job(organization_id, intent.worker, thread)
            job = None
            if intent.job is not None or worker is not None:
                try:
                    job = await self._resolve_job(
                        organization_id,
                        intent.job,
                        worker,
                        prefer_current=False,
                    )
                except CommandResolutionError:
                    if intent.job is not None:
                        raise
            return await self._failure_explain(organization_id, worker, job, intent, command)

        if family == "integration.require":
            require_permission(principal, "integrations.read")
            provider = (
                str(intent.arguments.get("provider") or intent.arguments.get("integration") or "")
                .strip()
                .lower()
            )
            if not provider:
                raise CommandResolutionError("Which integration do you need?")
            integration = await self._session.scalar(
                select(Integration).where(
                    Integration.organization_id == organization_id,
                    func.lower(Integration.provider) == provider,
                )
            )
            if integration is None or integration.status not in {
                "active",
                "connected",
                "healthy",
            }:
                return CommandReceipt(
                    status="waiting_integration",
                    message=f"{provider} needs to be connected before this work can continue.",
                    references=[],
                    command_id=command.id,
                    failure_category="missing_integration",
                    action_hints=[
                        {
                            "kind": "connect_integration",
                            "provider": provider,
                            "label": f"Connect {provider.replace('_', ' ').title()}",
                        }
                    ],
                )
            return CommandReceipt(
                status="completed",
                message=f"{integration.display_name} is connected.",
                references=[
                    self._ref(
                        "integration",
                        integration.id,
                        integration.display_name,
                    )
                ],
                command_id=command.id,
            )

        if family == "attachment.analyze":
            artifact_id = self._attachment_id(intent, source_message)
            if artifact_id is None:
                raise CommandResolutionError("Which uploaded attachment should I analyze?")
            analysis = await AttachmentIngestionService(
                self._session,
                self._gateway,
            ).analyze_existing(
                organization_id=organization_id,
                artifact_id=artifact_id,
                thread_id=thread.id,
                worker_id=thread.worker_id,
                question=str(intent.arguments.get("question") or source_message.content or ""),
            )
            if analysis.status in {"waiting_configuration", "waiting_ai"}:
                return CommandReceipt(
                    status="unavailable",
                    message=(
                        "The attachment is stored safely, but visual analysis is "
                        "waiting for the AI vision provider."
                    ),
                    references=[
                        self._ref("artifact", artifact_id, None),
                        self._ref("artifact_analysis", analysis.id, None),
                    ],
                    command_id=command.id,
                )
            if analysis.status == "failed":
                return CommandReceipt(
                    status="unavailable",
                    message="The attachment is stored, but its analysis could not be completed.",
                    references=[
                        self._ref("artifact", artifact_id, None),
                        self._ref("artifact_analysis", analysis.id, None),
                    ],
                    command_id=command.id,
                )
            summary = str(analysis.findings.get("summary") or "")
            if not summary:
                summary = str(analysis.findings.get("excerpt") or "")
            if not summary:
                summary = "Attachment analysis completed."
            return CommandReceipt(
                status="completed",
                message=summary[:12000],
                references=[
                    self._ref("artifact", artifact_id, None),
                    self._ref("artifact_analysis", analysis.id, None),
                ],
                command_id=command.id,
            )

        if family == "conversation.answer":
            response = await self._gateway.generate_text(
                role="conversation",
                system=(
                    "Answer the user's question using only AUTHORITATIVE_CONTEXT. "
                    "Never invent worker/job/run/result state. If the context does not "
                    "contain the answer, say what is missing. Do not reveal secrets or "
                    "internal credentials."
                ),
                prompt=(
                    "AUTHORITATIVE_CONTEXT:\n"
                    + json.dumps(context, separators=(",", ":"), default=str)
                    + "\n\nQUESTION:\n"
                    + source_message.content
                ),
                context=AIInvocationContext(
                    organization_id=organization_id,
                    worker_id=thread.worker_id,
                    thread_id=thread.id,
                    correlation_id=f"conversation:{source_message.id}",
                ),
                max_output_tokens=900,
            )
            return CommandReceipt(
                status="completed",
                message=response.text.strip(),
                command_id=command.id,
            )

        raise CommandResolutionError(
            f"I understood the request as {family}, but that operation is not available."
        )

    async def _prepare_existing_worker_job_payload(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        worker: Worker,
        source_message: ConversationMessage,
        source_thread_id: UUID,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Give AI-created Jobs the same origin-aware Browser authority as new Workers."""

        payload = {
            **arguments,
            "workerId": str(worker.id),
            "name": name,
        }
        origins = explicit_http_origins(source_message.content)
        if not origins:
            return payload, ()

        require_permission(principal, "integrations.manage")
        require_permission(principal, "capabilities.manage")
        require_permission(principal, "policies.manage")

        await ensure_managed_browser_origins(
            self._session,
            organization_id=organization_id,
            origins=origins,
            principal=principal,
            source=f"conversation:{source_message.id}",
        )

        need = CapabilityNeed(
            provider="browser",
            need=source_message.content[:500],
            actions=[],
        )
        resolution = await SemanticCapabilityResolver(
            self._session,
            self._gateway,
        ).resolve(
            organization_id=organization_id,
            needs=[need],
            required_browser_origins=origins,
            invocation_context=AIInvocationContext(
                organization_id=organization_id,
                worker_id=worker.id,
                thread_id=source_thread_id,
                correlation_id=f"job-capability:{source_message.id}",
            ),
        )
        if not resolution.scopes:
            raise CommandResolutionError(
                "I could not resolve a least-authority browser capability set "
                "for that website job."
            )

        raw_existing_scopes = payload.get("requiredCapabilities")
        existing_scopes = {
            str(scope)
            for scope in raw_existing_scopes
            if str(scope)
        } if isinstance(raw_existing_scopes, list) else set()
        raw_autonomy = payload.get("autonomy")
        autonomy: dict[str, Any] = (
            {str(key): value for key, value in raw_autonomy.items()}
            if isinstance(raw_autonomy, dict)
            else {}
        )
        autonomy.update(
            {
                "createdByAI": True,
                "sourceThreadId": str(source_thread_id),
                "sourceMessageId": str(source_message.id),
                "authorizedBrowserOrigins": list(origins),
                "capabilityMappings": list(resolution.mappings),
                "capabilityNeeds": [need.model_dump(mode="json")],
            }
        )
        payload["requiredCapabilities"] = sorted(existing_scopes | set(resolution.scopes))
        payload["autonomy"] = autonomy
        return payload, resolution.scopes

    def _attachment_id(
        self,
        intent: WorkerCommandIntent,
        source_message: ConversationMessage,
    ) -> UUID | None:
        raw = intent.arguments.get("artifactId")
        if raw:
            try:
                return UUID(str(raw))
            except ValueError as exc:
                raise CommandResolutionError("That attachment ID is invalid.") from exc
        for reference in list(source_message.artifact_references or []):
            value = reference.get("id") if isinstance(reference, dict) else reference
            if value is None:
                continue
            try:
                return UUID(str(value))
            except ValueError:
                continue
        return None

    async def _resolve_worker(
        self,
        organization_id: UUID,
        reference: EntityReference | None,
        thread: ConversationThread,
    ) -> Worker:
        if reference is None and thread.worker_id is not None:
            worker = await self._session.scalar(
                select(Worker).where(
                    Worker.organization_id == organization_id,
                    Worker.id == thread.worker_id,
                )
            )
            if worker is not None:
                return worker
        if reference is None:
            raise CommandResolutionError("Which worker do you mean?")

        if reference.id is not None:
            worker = await self._session.get(Worker, reference.id)
            if worker is None:
                raise CommandResolutionError("I could not find that worker.")
            if worker.organization_id != organization_id:
                raise CrossTenantReferenceError("Cross-tenant worker reference denied.")
            return worker

        name = str(reference.name or "").strip()
        if not name:
            raise CommandResolutionError("Which worker do you mean?")
        exact = list(
            (
                await self._session.scalars(
                    select(Worker).where(
                        Worker.organization_id == organization_id,
                        func.lower(Worker.name) == name.lower(),
                    )
                )
            ).all()
        )
        if len(exact) == 1:
            return exact[0]
        matches = exact or list(
            (
                await self._session.scalars(
                    select(Worker)
                    .where(
                        Worker.organization_id == organization_id,
                        Worker.name.ilike(f"%{name}%"),
                    )
                    .limit(6)
                )
            ).all()
        )
        if not matches:
            raise CommandResolutionError(f"I could not find a worker named {name}.")
        if len(matches) > 1:
            choices = ", ".join(worker.name for worker in matches[:5])
            raise CommandResolutionError(
                f"I found multiple workers matching {name}: {choices}. Which one?"
            )
        return matches[0]

    async def _resolve_worker_for_job(
        self,
        organization_id: UUID,
        reference: EntityReference | None,
        thread: ConversationThread,
    ) -> Worker | None:
        if reference is None and thread.worker_id is None:
            return None
        return await self._resolve_worker(organization_id, reference, thread)

    async def _resolve_job(
        self,
        organization_id: UUID,
        reference: EntityReference | None,
        worker: Worker | None,
        *,
        prefer_current: bool,
    ) -> Job:
        if reference is not None and reference.id is not None:
            job = await self._session.get(Job, reference.id)
            if job is None:
                raise CommandResolutionError("I could not find that job.")
            if job.organization_id != organization_id:
                raise CrossTenantReferenceError("Cross-tenant job reference denied.")
            if worker is not None and job.worker_id != worker.id:
                raise CommandResolutionError(f"That job does not belong to {worker.name}.")
            return job

        if reference is not None and reference.name:
            statement = select(Job).where(
                Job.organization_id == organization_id,
                func.lower(Job.name) == reference.name.strip().lower(),
                Job.status != "archived",
            )
            if worker is not None:
                statement = statement.where(Job.worker_id == worker.id)
            exact = list((await self._session.scalars(statement)).all())
            if len(exact) == 1:
                return exact[0]
            if len(exact) > 1:
                raise CommandResolutionError(
                    f"I found multiple jobs named {reference.name}. Which one?"
                )

        statement = select(Job).where(
            Job.organization_id == organization_id,
            Job.status != "archived",
        )
        if worker is not None:
            statement = statement.where(Job.worker_id == worker.id)
        jobs = list((await self._session.scalars(statement)).all())

        if reference is not None and reference.name:
            named = [job for job in jobs if reference.name.lower() in job.name.lower()]
            if len(named) == 1:
                return named[0]
            if len(named) > 1:
                names = ", ".join(job.name for job in named[:5])
                raise CommandResolutionError(
                    f"I found multiple jobs matching {reference.name}: {names}. Which one?"
                )
            raise CommandResolutionError(f"I could not find a job named {reference.name}.")

        if prefer_current and jobs:
            active_job_ids: set[UUID] = set()
            open_items = await self._session.scalars(
                select(WorkItem).where(
                    WorkItem.organization_id == organization_id,
                    WorkItem.job_id.in_([job.id for job in jobs]),
                    WorkItem.status.in_(
                        [
                            "queued",
                            "running",
                            "waiting_approval",
                            "waiting_ai",
                            "waiting_configuration",
                        ]
                    ),
                )
            )
            for item in open_items.all():
                active_job_ids.add(item.job_id)
            current = [job for job in jobs if job.id in active_job_ids]
            if len(current) == 1:
                return current[0]
            if len(current) > 1:
                names = ", ".join(job.name for job in current[:5])
                raise CommandResolutionError(
                    f"More than one job is currently active: {names}. Which one?"
                )

        if len(jobs) == 1:
            return jobs[0]
        if not jobs:
            raise CommandResolutionError("I could not find a matching job.")
        names = ", ".join(job.name for job in jobs[:5])
        raise CommandResolutionError(f"I found multiple possible jobs: {names}. Which one?")

    async def _resolve_policy(
        self,
        organization_id: UUID,
        reference: EntityReference | None,
    ) -> Policy:
        if reference is None:
            raise CommandResolutionError("Which policy do you mean?")
        if reference.id is not None:
            policy = await self._session.get(Policy, reference.id)
            if policy is None:
                raise CommandResolutionError("I could not find that policy.")
            if policy.organization_id != organization_id:
                raise CrossTenantReferenceError("Cross-tenant policy reference denied.")
            return policy
        name = str(reference.name or "").strip()
        rows = list(
            (
                await self._session.scalars(
                    select(Policy).where(
                        Policy.organization_id == organization_id,
                        Policy.name.ilike(f"%{name}%"),
                    )
                )
            ).all()
        )
        if len(rows) != 1:
            raise CommandResolutionError(
                "I could not resolve that policy uniquely. Which policy do you mean?"
            )
        return rows[0]

    async def _resolve_directive(
        self,
        organization_id: UUID,
        worker: Worker,
        arguments: dict[str, Any],
    ) -> WorkerDirective:
        raw_id = arguments.get("directiveId")
        if raw_id:
            try:
                directive_id = UUID(str(raw_id))
            except ValueError as exc:
                raise CommandResolutionError("That directive ID is invalid.") from exc
            directive = await self._session.get(WorkerDirective, directive_id)
            if directive is None:
                raise CommandResolutionError("I could not find that instruction.")
            if directive.organization_id != organization_id or directive.worker_id != worker.id:
                raise CrossTenantReferenceError("Directive reference denied.")
            return directive
        text = str(arguments.get("instruction") or arguments.get("text") or "").strip()
        statement = select(WorkerDirective).where(
            WorkerDirective.organization_id == organization_id,
            WorkerDirective.worker_id == worker.id,
            WorkerDirective.status == "active",
        )
        rows = list((await self._session.scalars(statement)).all())
        if text:
            rows = [row for row in rows if text.lower() in row.text.lower()]
        if len(rows) != 1:
            raise CommandResolutionError(
                "I could not resolve that standing instruction uniquely. Which one?"
            )
        return rows[0]

    async def _worker_status(
        self,
        organization_id: UUID,
        worker: Worker,
        command: ConversationCommand,
    ) -> CommandReceipt:
        jobs = list(
            (
                await self._session.scalars(
                    select(Job)
                    .where(
                        Job.organization_id == organization_id,
                        Job.worker_id == worker.id,
                        Job.status != "archived",
                    )
                    .order_by(desc(Job.updated_at))
                )
            ).all()
        )
        job_ids = [job.id for job in jobs]
        open_item = None
        if job_ids:
            open_item = await self._session.scalar(
                select(WorkItem)
                .where(
                    WorkItem.organization_id == organization_id,
                    WorkItem.job_id.in_(job_ids),
                    WorkItem.status.in_(
                        [
                            "queued",
                            "running",
                            "waiting_approval",
                            "waiting_ai",
                            "waiting_configuration",
                        ]
                    ),
                )
                .order_by(desc(WorkItem.created_at))
                .limit(1)
            )
        if open_item is not None:
            job = next(
                (candidate for candidate in jobs if candidate.id == open_item.job_id),
                None,
            )
            job_name = job.name if job is not None else "current job"
            message = f"{worker.name} is {worker.status}. {job_name} is {open_item.status}."
            references = [
                self._ref("worker", worker.id, worker.name),
                self._ref("work_item", open_item.id, None),
            ]
            if job is not None:
                references.append(self._ref("job", job.id, job.name))
        else:
            active = [job.name for job in jobs if job.status == "active"]
            message = f"{worker.name} is {worker.status}. " + (
                f"Active jobs: {', '.join(active[:5])}."
                if active
                else "There is no open work right now."
            )
            references = [self._ref("worker", worker.id, worker.name)]
        return CommandReceipt(
            status="completed",
            message=message,
            references=references,
            command_id=command.id,
        )

    async def _job_status(
        self,
        organization_id: UUID,
        job: Job,
        command: ConversationCommand,
    ) -> CommandReceipt:
        item = await self._latest_work_item(organization_id, job, statuses=None)
        message = f"{job.name} is {job.status}."
        refs = [self._ref("job", job.id, job.name)]
        if item is not None:
            message += f" Its latest work item is {item.status}."
            refs.append(self._ref("work_item", item.id, None))
        return CommandReceipt(
            status="completed",
            message=message,
            references=refs,
            command_id=command.id,
        )

    async def _latest_work_item(
        self,
        organization_id: UUID,
        job: Job,
        statuses: set[str] | None,
    ) -> WorkItem | None:
        statement = select(WorkItem).where(
            WorkItem.organization_id == organization_id,
            WorkItem.job_id == job.id,
        )
        if statuses:
            statement = statement.where(WorkItem.status.in_(list(statuses)))
        return await self._session.scalar(statement.order_by(desc(WorkItem.created_at)).limit(1))

    async def _cancel_open_work(
        self,
        organization_id: UUID,
        job: Job,
        principal: HumanPrincipal,
    ) -> None:
        rows = await self._session.scalars(
            select(WorkItem).where(
                WorkItem.organization_id == organization_id,
                WorkItem.job_id == job.id,
                WorkItem.status.in_(
                    [
                        "queued",
                        "running",
                        "waiting_approval",
                        "waiting_ai",
                        "waiting_configuration",
                    ]
                ),
            )
        )
        for item in rows.all():
            await cancel_item(
                self._session,
                organization_id,
                item.id,
                principal,
            )

    async def _pause_job_schedule(
        self,
        organization_id: UUID,
        job: Job,
        principal: HumanPrincipal,
    ) -> None:
        revision = await current_revision(self._session, job)
        definition = dict(revision.definition or {})
        raw = definition.get("triggerConfig")
        if not isinstance(raw, dict):
            return
        schedule = raw.get("schedule")
        if not isinstance(schedule, dict):
            return
        await save_trigger_config(
            self._session,
            organization_id,
            job.id,
            principal,
            {
                "schedule": {**schedule, "enabled": False},
                "apiEnabled": bool(raw.get("apiEnabled", False)),
                "internalEventKeys": list(raw.get("internalEventKeys", [])),
                "dependencyJobIds": list(raw.get("dependencyJobIds", [])),
            },
        )

    async def _resume_job_schedule(
        self,
        organization_id: UUID,
        job: Job,
        principal: HumanPrincipal,
    ) -> None:
        revision = await current_revision(self._session, job)
        definition = dict(revision.definition or {})
        raw = definition.get("triggerConfig")
        if not isinstance(raw, dict):
            return
        schedule = raw.get("schedule")
        if not isinstance(schedule, dict):
            return
        await save_trigger_config(
            self._session,
            organization_id,
            job.id,
            principal,
            {
                "schedule": {**schedule, "enabled": True},
                "apiEnabled": bool(raw.get("apiEnabled", False)),
                "internalEventKeys": list(raw.get("internalEventKeys", [])),
                "dependencyJobIds": list(raw.get("dependencyJobIds", [])),
            },
        )

    async def _change_schedule(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        job: Job,
        family: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        require_permission(principal, "jobs.manage")
        revision = await current_revision(self._session, job)
        definition = dict(revision.definition or {})
        raw = definition.get("triggerConfig")
        existing = dict(raw) if isinstance(raw, dict) else {}
        raw_schedule = existing.get("schedule")
        current_schedule: dict[str, Any] = (
            dict(raw_schedule) if isinstance(raw_schedule, dict) else {}
        )
        proposed = arguments.get("schedule")
        proposed_schedule = dict(proposed) if isinstance(proposed, dict) else {}

        if family == "schedule.pause":
            schedule = {**current_schedule, "enabled": False}
        elif family == "schedule.resume":
            if not current_schedule:
                raise CommandResolutionError(f"{job.name} does not have a schedule to resume.")
            schedule = {**current_schedule, "enabled": True}
        else:
            schedule = {**current_schedule, **proposed_schedule, "enabled": True}

        cadence = str(schedule.get("cadence") or "")
        if cadence not in {"daily", "weekly", "interval"}:
            raise CommandResolutionError(
                "I need a valid canonical schedule cadence: daily, weekly, or interval."
            )
        if cadence == "weekly":
            weekdays = list(schedule.get("weekdays") or [])
            if not weekdays:
                raise CommandResolutionError("Which weekday(s) should this job run?")
        config = await save_trigger_config(
            self._session,
            organization_id,
            job.id,
            principal,
            {
                "schedule": schedule,
                "apiEnabled": bool(existing.get("apiEnabled", False)),
                "internalEventKeys": list(existing.get("internalEventKeys", [])),
                "dependencyJobIds": list(existing.get("dependencyJobIds", [])),
            },
        )
        await job_status_v1(
            organization_id,
            job.id,
            {"status": "active"},
            principal,
            self._session,
        )
        return config

    def _policy_payload(
        self,
        intent: WorkerCommandIntent,
        worker: Worker | None,
    ) -> dict[str, Any]:
        args = dict(intent.arguments)
        boundary = str(args.get("approvalBoundary") or "").strip().lower()
        selectors = args.get("selectors")
        selectors = dict(selectors) if isinstance(selectors, dict) else {}
        if boundary == "submit":
            selectors["scopes"] = ["browser.form.submit"]
        if worker is not None:
            selectors["agentIds"] = [str(worker.agent_identity_id)]
        name = str(args.get("name") or "").strip()
        if not name:
            name = (
                f"{worker.name} submission approval"
                if worker is not None and boundary == "submit"
                else "Conversation policy"
            )
        effect = str(args.get("effect") or ("require_approval" if boundary else "deny"))
        if effect not in {"allow", "deny", "require_approval"}:
            raise CommandResolutionError("That policy effect is not supported.")
        return {
            "name": name,
            "description": str(args.get("description") or args.get("directive") or ""),
            "effect": effect,
            "priority": int(args.get("priority") or 500),
            "selectors": selectors,
        }

    async def _result_query(
        self,
        organization_id: UUID,
        worker: Worker | None,
        intent: WorkerCommandIntent,
        command: ConversationCommand,
    ) -> CommandReceipt:
        statement = (
            select(Result)
            .where(Result.organization_id == organization_id)
            .order_by(desc(Result.created_at))
            .limit(10)
        )
        if worker is not None:
            statement = statement.where(Result.worker_id == worker.id)
        results = list((await self._session.scalars(statement)).all())
        if not results:
            return CommandReceipt(
                status="completed",
                message="There are no matching recent results.",
                command_id=command.id,
            )
        summaries: list[str] = []
        refs: list[CommandReference] = []
        for result in results[:5]:
            version = await self._session.scalar(
                select(ResultVersion).where(
                    ResultVersion.result_id == result.id,
                    ResultVersion.version == result.latest_version,
                )
            )
            body = dict(version.body or {}) if version is not None else {}
            summary = str(body.get("summary") or result.title).strip()
            summaries.append(f"{result.title}: {summary[:300]}")
            refs.append(self._ref("result", result.id, result.title))
        return CommandReceipt(
            status="completed",
            message="Recent results: " + " | ".join(summaries),
            result_references=refs,
            command_id=command.id,
        )

    async def _failure_explain(
        self,
        organization_id: UUID,
        worker: Worker | None,
        job: Job | None,
        intent: WorkerCommandIntent,
        command: ConversationCommand,
    ) -> CommandReceipt:
        run = None
        if intent.run is not None and intent.run.id is not None:
            candidate = await self._session.get(Run, intent.run.id)
            if candidate is None:
                raise CommandResolutionError("I could not find that run.")
            if candidate.organization_id != organization_id:
                raise CrossTenantReferenceError("Cross-tenant run reference denied.")
            run = candidate
        if run is None:
            statement = select(Run).where(
                Run.organization_id == organization_id,
                Run.status == "failed",
            )
            if job is not None:
                statement = statement.join(WorkItem, Run.work_item_id == WorkItem.id).where(
                    WorkItem.job_id == job.id
                )
            elif worker is not None:
                job_ids = list(
                    (
                        await self._session.scalars(
                            select(Job.id).where(
                                Job.organization_id == organization_id,
                                Job.worker_id == worker.id,
                            )
                        )
                    ).all()
                )
                if not job_ids:
                    raise CommandResolutionError(
                        f"{worker.name} has no jobs with failure evidence."
                    )
                statement = statement.join(WorkItem, Run.work_item_id == WorkItem.id).where(
                    WorkItem.job_id.in_(job_ids)
                )
            run = await self._session.scalar(statement.order_by(desc(Run.created_at)).limit(1))
        if run is None:
            return CommandReceipt(
                status="completed",
                message="I could not find a failed run matching that request.",
                command_id=command.id,
            )
        steps = list(
            (
                await self._session.scalars(
                    select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.step_index)
                )
            ).all()
        )
        errors: list[str] = []
        for step in steps:
            output = dict(step.output or {})
            error = output.get("error")
            if error:
                errors.append(f"step {step.step_index}: {str(error)[:600]}")
        evidence = "; ".join(errors[:5]) or str(run.result_summary or "No step error was recorded.")
        return CommandReceipt(
            status="completed",
            message=f"Run {run.id} failed. Recorded evidence: {evidence}",
            references=[self._ref("run", run.id, None)],
            command_id=command.id,
        )

    async def _worker_event(self, worker: Worker, status: str) -> None:
        await TransactionalOutbox(self._session).enqueue(
            topic="worker.status.changed",
            aggregate_type="worker",
            aggregate_id=str(worker.id),
            payload={
                "organization_id": str(worker.organization_id),
                "worker_id": str(worker.id),
                "resource_id": str(worker.id),
                "status": status,
            },
        )

    async def _event(
        self,
        topic: str,
        command: ConversationCommand,
        *,
        thread: ConversationThread,
        worker_id: UUID | None,
        payload: dict[str, object],
    ) -> None:
        await TransactionalOutbox(self._session).enqueue(
            topic=topic,
            aggregate_type="conversation_command",
            aggregate_id=str(command.id),
            payload={
                "organization_id": str(command.organization_id),
                "worker_id": str(worker_id) if worker_id else None,
                "resource_id": str(thread.id),
                "thread_id": str(thread.id),
                "command_id": str(command.id),
                **payload,
            },
        )

    def _receipt(
        self,
        status: str,
        message: str,
        obj: Any,
        *,
        object_type: str = "worker",
    ) -> CommandReceipt:
        return CommandReceipt(
            status=status,  # type: ignore[arg-type]
            message=message,
            references=[
                self._ref(
                    object_type,
                    obj.id,
                    getattr(obj, "name", None),
                )
            ],
        )

    def _ref(
        self,
        object_type: str,
        object_id: UUID | str,
        name: str | None,
    ) -> CommandReference:
        return CommandReference(
            type=object_type,
            id=str(object_id),
            name=name,
        )
