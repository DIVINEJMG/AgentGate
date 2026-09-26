from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.governance_routes import _create_policy
from app.api.jobs_routes import (
    _provision_managed_job_authority,
    create_job,
    current_revision,
    job_status_v1,
    queue_job,
    save_trigger_config,
    update_job,
)
from app.api.product_common import require_permission
from app.api.workforce_routes import (
    automatic_agent,
    create_role,
    create_worker,
    set_worker_status,
    update_worker,
)
from app.application.services.browser_origin_authority import (
    ensure_managed_browser_origins,
    explicit_http_origins,
)
from app.application.services.capability_autoresolver import (
    CapabilityResolution,
    SemanticCapabilityResolver,
)
from app.application.services.schedule_inference import (
    CompiledSchedule,
    compile_schedule,
)
from app.application.services.worker_draft import WorkerDraftGenerator
from app.application.services.worker_memory import WorkerMemoryService, contains_secret_material
from app.domain.ai.providers import AIGateway, AIInvocationContext, AIProviderError
from app.domain.conversation.intent import CommandReference
from app.domain.identity.principals import HumanPrincipal
from app.domain.workforce.drafts import (
    ApprovalBoundaryDraft,
    JobDraft,
    WorkerDraft,
)
from app.infrastructure.database.models import (
    HumanIdentity,
    Integration,
    Job,
    OrganizationMembership,
    Worker,
    WorkerDirective,
    WorkforceRole,
)


@dataclass(frozen=True, slots=True)
class PreparedJob:
    draft: JobDraft
    capabilities: CapabilityResolution
    schedule: CompiledSchedule
    missing_integrations: tuple[str, ...]
    browser_origins: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkerAutonomyResult:
    worker: Worker
    jobs: tuple[Job, ...]
    draft: WorkerDraft
    missing_integrations: tuple[str, ...]
    references: tuple[CommandReference, ...]


def _provider_id(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "drive": "google_drive",
        "calendar": "google_calendar",
        "web": "browser",
        "chrome": "browser",
    }.get(normalized, normalized)


class WorkerAutonomyService:
    def __init__(
        self,
        session: AsyncSession,
        gateway: AIGateway,
    ) -> None:
        self._session = session
        self._gateway = gateway

    async def create_from_instruction(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        instruction: str,
        authoritative_context: dict[str, Any],
        source_thread_id: UUID,
        source_message_id: UUID,
    ) -> WorkerAutonomyResult:
        require_permission(principal, "workforce.manage")
        require_permission(principal, "jobs.manage")
        invocation = AIInvocationContext(
            organization_id=organization_id,
            thread_id=source_thread_id,
            correlation_id=f"worker-draft:{source_message_id}",
        )
        draft = await WorkerDraftGenerator(self._gateway).generate(
            instruction=instruction,
            authoritative_context=authoritative_context,
            invocation_context=invocation,
        )
        memory_candidates = [
            draft.charter,
            *draft.responsibilities,
            *draft.standing_instructions,
            *[
                value
                for job in draft.initial_jobs
                for value in (
                    job.objective,
                    job.instructions,
                    *job.completion_criteria,
                )
            ],
        ]
        if any(contains_secret_material(value) for value in memory_candidates):
            raise ValueError(
                "The Worker draft contains credential-like material and cannot be persisted."
            )

        prepared: list[PreparedJob] = []
        has_capability_needs = False
        has_policy_boundaries = False
        explicit_origins = explicit_http_origins(instruction)
        draft_uses_browser = any(
            _provider_id(need.provider) == "browser"
            for job in draft.initial_jobs
            for need in job.capability_needs
        )
        if (
            explicit_origins
            and draft_uses_browser
            and "integrations.manage" in principal.permissions
        ):
            await ensure_managed_browser_origins(
                self._session,
                organization_id=organization_id,
                origins=explicit_origins,
                principal=principal,
                source=f"conversation:{source_message_id}",
            )

        connected_providers = set(
            (
                await self._session.scalars(
                    select(Integration.provider).where(
                        Integration.organization_id == organization_id,
                        Integration.status == "connected",
                    )
                )
            ).all()
        )

        resolver = SemanticCapabilityResolver(self._session, self._gateway)
        for index, job_draft in enumerate(draft.initial_jobs):
            has_capability_needs = has_capability_needs or bool(job_draft.capability_needs)
            has_policy_boundaries = has_policy_boundaries or bool(job_draft.approval_boundaries)
            browser_origins = (
                explicit_origins
                if any(
                    _provider_id(need.provider) == "browser"
                    for need in job_draft.capability_needs
                )
                else ()
            )
            resolution = await resolver.resolve(
                organization_id=organization_id,
                needs=job_draft.capability_needs,
                required_browser_origins=browser_origins,
                invocation_context=AIInvocationContext(
                    organization_id=organization_id,
                    thread_id=source_thread_id,
                    correlation_id=f"capability-draft:{source_message_id}:{index}",
                ),
            )
            if job_draft.capability_needs and not resolution.scopes:
                unresolved_connected = {
                    _provider_id(need.provider)
                    for need in job_draft.capability_needs
                    if _provider_id(need.provider) in connected_providers
                }
                if unresolved_connected:
                    raise ValueError(
                        "The capability resolver could not map the requested work to "
                        "an exact least-authority capability set."
                    )

            explicit_requirements = {
                _provider_id(requirement.provider)
                for requirement in job_draft.integration_requirements
            }
            explicit_missing = {
                provider
                for provider in explicit_requirements
                if provider not in connected_providers
            }
            missing = tuple(sorted(set(resolution.missing_integrations) | explicit_missing))
            prepared.append(
                PreparedJob(
                    draft=job_draft,
                    capabilities=resolution,
                    schedule=compile_schedule(job_draft.schedule),
                    missing_integrations=missing,
                    browser_origins=browser_origins,
                )
            )

        if has_capability_needs:
            require_permission(principal, "capabilities.manage")
            # Managed authority provisioning creates least-authority policies too.
            require_permission(principal, "policies.manage")
        if has_policy_boundaries:
            require_permission(principal, "policies.manage")

        role = await self._find_or_create_role(
            organization_id=organization_id,
            principal=principal,
            draft=draft,
        )
        supervisor_id = await self._resolve_supervisor(
            organization_id=organization_id,
            principal=principal,
            supervisor_name=draft.supervisor_name,
        )
        existing_workers = list(
            (
                await self._session.scalars(
                    select(Worker).where(
                        Worker.organization_id == organization_id,
                        func.lower(Worker.name) == draft.suggested_name.lower(),
                        Worker.status != "archived",
                    )
                )
            ).all()
        )
        if len(existing_workers) > 1:
            raise ValueError(
                f"More than one Worker is named {draft.suggested_name}; "
                "the target must be resolved explicitly."
            )
        if existing_workers:
            worker = existing_workers[0]
            profile = worker.profile if isinstance(worker.profile, dict) else {}
            if has_capability_needs and profile.get("agentIdentityProvisioning") != "automatic":
                raise ValueError(
                    f"{worker.name} uses a manually provisioned identity, so Aduoryn "
                    "will not automatically expand its authority."
                )
            worker = await update_worker(
                self._session,
                organization_id,
                worker.id,
                principal,
                {
                    "roleId": str(role.id),
                    "supervisorUserId": str(supervisor_id),
                    "name": draft.suggested_name,
                    "department": draft.department,
                    "description": draft.charter,
                    "responsibilities": draft.responsibilities,
                    "instructions": "\n".join(draft.standing_instructions),
                },
            )
        else:
            agent, _credential = await automatic_agent(
                self._session,
                organization_id,
                principal,
                draft.suggested_name,
            )
            worker = await create_worker(
                self._session,
                organization_id,
                principal,
                {
                    "agentIdentityId": str(agent.id),
                    "roleId": str(role.id),
                    "supervisorUserId": str(supervisor_id),
                    "name": draft.suggested_name,
                    "department": draft.department,
                    "description": draft.charter,
                    "responsibilities": draft.responsibilities,
                    "instructions": "\n".join(draft.standing_instructions),
                },
                provisioning="automatic",
            )
        await set_worker_status(
            self._session,
            organization_id,
            worker.id,
            principal,
            "active",
        )
        worker = await self._session.get(Worker, worker.id)
        assert worker is not None

        memory = WorkerMemoryService(self._session)
        await memory.seed_identity(
            worker=worker,
            role=draft.role,
            charter=draft.charter,
            responsibilities=draft.responsibilities,
            source_id=str(source_message_id),
        )
        for instruction_text in draft.standing_instructions:
            directive = WorkerDirective(
                organization_id=organization_id,
                worker_id=worker.id,
                text=instruction_text,
                status="active",
                created_by=principal.user_id,
                source_thread_id=source_thread_id,
                source_message_id=source_message_id,
            )
            self._session.add(directive)
            await memory.add_standing_memory(
                worker=worker,
                instruction=instruction_text,
                source_thread_id=source_thread_id,
                source_message_id=source_message_id,
            )
        await self._session.commit()

        created_jobs: list[Job] = []
        all_missing: set[str] = set()
        references: list[CommandReference] = [
            CommandReference(type="worker", id=str(worker.id), name=worker.name)
        ]
        for index, item in enumerate(prepared):
            job = await self._create_job(
                organization_id=organization_id,
                principal=principal,
                worker=worker,
                prepared=item,
                source_thread_id=source_thread_id,
                source_message_id=source_message_id,
                index=index,
            )
            created_jobs.append(job)
            all_missing.update(item.missing_integrations)
            references.append(CommandReference(type="job", id=str(job.id), name=job.name))

        for provider in sorted(all_missing):
            references.append(
                CommandReference(
                    type="integration_requirement",
                    id=provider,
                    name=f"Connect {provider}",
                )
            )

        return WorkerAutonomyResult(
            worker=worker,
            jobs=tuple(created_jobs),
            draft=draft,
            missing_integrations=tuple(sorted(all_missing)),
            references=tuple(references),
        )

    async def _resolve_supervisor(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        supervisor_name: str | None,
    ) -> UUID:
        name = (supervisor_name or "").strip()
        if not name:
            return principal.user_id
        rows = list(
            (
                await self._session.scalars(
                    select(HumanIdentity)
                    .join(
                        OrganizationMembership,
                        OrganizationMembership.user_id == HumanIdentity.id,
                    )
                    .where(
                        OrganizationMembership.organization_id == organization_id,
                        func.lower(func.coalesce(HumanIdentity.display_name, "")) == name.lower(),
                    )
                    .limit(3)
                )
            ).all()
        )
        if len(rows) != 1:
            raise ValueError(
                f"Supervisor {name} could not be resolved uniquely to an organization member."
            )
        return rows[0].id

    async def _find_or_create_role(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        draft: WorkerDraft,
    ) -> WorkforceRole:
        role = await self._session.scalar(
            select(WorkforceRole).where(
                WorkforceRole.organization_id == organization_id,
                func.lower(WorkforceRole.name) == draft.role.lower(),
            )
        )
        if role is not None:
            return role
        return await create_role(
            self._session,
            organization_id,
            principal,
            {
                "name": draft.role,
                "purpose": draft.charter,
                "defaultInstructions": "\n".join(draft.standing_instructions),
                "defaultResponsibilities": draft.responsibilities,
                "recommendedCapabilities": [],
            },
        )

    async def _create_job(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        worker: Worker,
        prepared: PreparedJob,
        source_thread_id: UUID,
        source_message_id: UUID,
        index: int,
    ) -> Job:
        draft = prepared.draft
        autonomy = {
            "createdByAI": True,
            "sourceThreadId": str(source_thread_id),
            "sourceMessageId": str(source_message_id),
            "scheduleIntent": draft.schedule.model_dump(mode="json"),
            "humanSchedule": prepared.schedule.human_readable,
            "startWhenReady": draft.start_when_ready,
            "stopAfterNextRun": prepared.schedule.stop_after_next_run,
            "missingIntegrations": list(prepared.missing_integrations),
            "capabilityMappings": list(prepared.capabilities.mappings),
            "capabilityNeeds": [need.model_dump(mode="json") for need in draft.capability_needs],
            "authorizedBrowserOrigins": list(prepared.browser_origins),
        }
        job_payload = {
            "workerId": str(worker.id),
            "name": draft.name,
            "description": draft.objective,
            "objective": draft.objective,
            "instructions": draft.instructions,
            "completionCriteria": draft.completion_criteria,
            "requiredCapabilities": list(prepared.capabilities.scopes),
            "integrationRequirements": list(prepared.missing_integrations),
            "autonomy": autonomy,
        }
        matches = list(
            (
                await self._session.scalars(
                    select(Job).where(
                        Job.organization_id == organization_id,
                        Job.worker_id == worker.id,
                        func.lower(Job.name) == draft.name.lower(),
                        Job.status != "archived",
                    )
                )
            ).all()
        )
        if len(matches) > 1:
            raise ValueError(f"More than one Job is named {draft.name} for {worker.name}.")
        if matches:
            job = await update_job(
                self._session,
                organization_id,
                matches[0].id,
                principal,
                job_payload,
            )
        else:
            job = await create_job(
                self._session,
                organization_id,
                principal,
                job_payload,
            )

        if prepared.capabilities.scopes:
            await _provision_managed_job_authority(
                self._session,
                organization_id,
                worker,
                principal,
                list(prepared.capabilities.scopes),
            )

        await self._apply_approval_boundaries(
            organization_id=organization_id,
            principal=principal,
            worker=worker,
            job=job,
            boundaries=draft.approval_boundaries,
            resolved_scopes=set(prepared.capabilities.scopes),
            source_message_id=source_message_id,
            index=index,
        )

        if prepared.schedule.trigger_config is not None:
            await save_trigger_config(
                self._session,
                organization_id,
                job.id,
                principal,
                prepared.schedule.trigger_config,
            )

        if prepared.missing_integrations:
            await job_status_v1(
                organization_id,
                job.id,
                {"status": "waiting_integration"},
                principal,
                self._session,
            )
            return job

        await job_status_v1(
            organization_id,
            job.id,
            {"status": "active"},
            principal,
            self._session,
        )
        if prepared.schedule.once_at is not None:
            await queue_job(
                self._session,
                organization_id,
                job,
                principal,
                trigger={
                    "type": "schedule",
                    "requestedByType": "human",
                    "requestedBy": str(principal.user_id),
                    "requestedAt": datetime.now(UTC).isoformat(),
                    "scheduledFor": prepared.schedule.once_at.isoformat(),
                    "dedupeKey": f"f31-once:{job.id}:{prepared.schedule.once_at.isoformat()}",
                    "configId": None,
                },
                scheduled_at=prepared.schedule.once_at,
            )
        elif draft.start_when_ready and draft.schedule.kind == "manual":
            await queue_job(
                self._session,
                organization_id,
                job,
                principal,
            )
        return job

    async def _apply_approval_boundaries(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        worker: Worker,
        job: Job,
        boundaries: list[ApprovalBoundaryDraft],
        resolved_scopes: set[str],
        source_message_id: UUID,
        index: int,
    ) -> None:
        for boundary_index, boundary in enumerate(boundaries):
            selected: set[str] = set()
            if boundary.kind == "submit_requires_approval":
                selected = {scope for scope in resolved_scopes if scope == "browser.form.submit"}
            elif boundary.kind == "external_send_requires_approval":
                selected = {
                    scope
                    for scope in resolved_scopes
                    if scope.endswith((".messages.create", ".messages.send", ".send"))
                }
            elif boundary.kind == "write_requires_approval":
                selected = {
                    scope
                    for scope in resolved_scopes
                    if any(
                        token in scope
                        for token in (
                            ".create",
                            ".update",
                            ".submit",
                            ".type",
                            ".fill",
                            ".upload",
                            ".delete",
                        )
                    )
                }
            elif boundary.kind == "allowed_origins_only":
                # F30 Browser owns origin enforcement. Persist the human boundary as
                # governed job metadata/directive; never broaden Browser integration policy.
                directive = WorkerDirective(
                    organization_id=organization_id,
                    worker_id=worker.id,
                    text=(
                        boundary.reason
                        or (
                            "Operate only on these allowed browser origins: "
                            + ", ".join(boundary.allowed_origins)
                            if boundary.allowed_origins
                            else "Operate only on the browser origins explicitly connected and allowed."
                        )
                    ),
                    status="active",
                    created_by=principal.user_id,
                )
                self._session.add(directive)
                await self._session.flush()
                continue

            if not selected:
                continue
            await _create_policy(
                self._session,
                organization_id,
                principal,
                {
                    "name": (
                        f"AI worker approval · {job.id} · "
                        f"{source_message_id.hex[:8]}-{index}-{boundary_index}"
                    ),
                    "description": boundary.reason
                    or "AI-generated approval boundary from Worker creation.",
                    "effect": "require_approval",
                    "priority": 700,
                    "selectors": {
                        "agentIds": [str(worker.agent_identity_id)],
                        "resourceIds": [],
                        "actions": [],
                        "scopes": sorted(selected),
                        "risks": [],
                    },
                },
            )


class AutonomyReadinessService:
    """Re-evaluate waiting AI-created jobs after integrations become connected."""

    def __init__(self, session: AsyncSession, gateway: AIGateway) -> None:
        self._session = session
        self._gateway = gateway

    async def reconcile(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
    ) -> list[UUID]:
        del principal
        connected = set(
            (
                await self._session.scalars(
                    select(Integration.provider).where(
                        Integration.organization_id == organization_id,
                        Integration.status == "connected",
                    )
                )
            ).all()
        )
        jobs = list(
            (
                await self._session.scalars(
                    select(Job).where(
                        Job.organization_id == organization_id,
                        Job.status == "waiting_integration",
                    )
                )
            ).all()
        )
        activated: list[UUID] = []
        for job in jobs:
            revision = await current_revision(self._session, job)
            definition = dict(revision.definition) if isinstance(revision.definition, dict) else {}
            raw_autonomy = definition.get("autonomy")
            autonomy: dict[str, object] = (
                {str(key): value for key, value in raw_autonomy.items()}
                if isinstance(raw_autonomy, dict)
                else {}
            )
            raw_needs = autonomy.get("capabilityNeeds")
            needs = []
            if isinstance(raw_needs, list):
                from app.domain.workforce.drafts import CapabilityNeed

                needs = [
                    CapabilityNeed.model_validate(item)
                    for item in raw_needs
                    if isinstance(item, dict)
                ]

            try:
                resolution = await SemanticCapabilityResolver(self._session, self._gateway).resolve(
                    organization_id=organization_id,
                    needs=needs,
                    invocation_context=AIInvocationContext(
                        organization_id=organization_id,
                        worker_id=job.worker_id,
                        job_id=job.id,
                        correlation_id=f"integration-ready:{job.id}",
                    ),
                )
            except AIProviderError:
                # A provider outage/config gap must never turn a successful
                # integration connection into an API failure. Leave the Job
                # waiting until the next readiness reconciliation.
                continue
            explicit = {
                str(value) for value in definition.get("integrationRequirements", []) if str(value)
            }
            # Explicit requirements that are now connected are satisfied; resolver
            # still reports providers that lack a usable connected capability.
            remaining = (explicit - connected) | set(resolution.missing_integrations)
            if remaining:
                continue

            worker = await self._session.get(Worker, job.worker_id)
            if worker is None:
                continue
            system = HumanPrincipal(
                user_id=revision.created_by,
                organization_id=organization_id,
                membership_id=UUID(int=0),
                role="system",
                permissions=frozenset(
                    {
                        "jobs.manage",
                        "jobs.run",
                        "capabilities.manage",
                        "policies.manage",
                    }
                ),
            )
            autonomy["missingIntegrations"] = []
            autonomy["capabilityMappings"] = list(resolution.mappings)
            await update_job(
                self._session,
                organization_id,
                job.id,
                system,
                {
                    "requiredCapabilities": list(resolution.scopes),
                    "integrationRequirements": [],
                    "autonomy": autonomy,
                },
            )
            if resolution.scopes:
                await _provision_managed_job_authority(
                    self._session,
                    organization_id,
                    worker,
                    system,
                    list(resolution.scopes),
                )
            await job_status_v1(
                organization_id,
                job.id,
                {"status": "active"},
                system,
                self._session,
            )

            raw_schedule = autonomy.get("scheduleIntent")
            schedule_kind = (
                str(raw_schedule.get("kind") or "manual")
                if isinstance(raw_schedule, dict)
                else "manual"
            )
            if bool(autonomy.get("startWhenReady")) and schedule_kind == "manual":
                await queue_job(self._session, organization_id, job, system)
            if bool(autonomy.get("startWhenReady")) and schedule_kind == "once":
                raw_once = raw_schedule.get("once_at") if isinstance(raw_schedule, dict) else None
                if raw_once:
                    try:
                        when = datetime.fromisoformat(str(raw_once))
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=UTC)
                    except ValueError:
                        when = datetime.now(UTC)
                    when = max(when.astimezone(UTC), datetime.now(UTC))
                    await queue_job(
                        self._session,
                        organization_id,
                        job,
                        system,
                        scheduled_at=when,
                        trigger={
                            "type": "schedule",
                            "requestedByType": "system",
                            "requestedBy": str(revision.created_by),
                            "requestedAt": datetime.now(UTC).isoformat(),
                            "scheduledFor": when.isoformat(),
                            "dedupeKey": f"f31-ready:{job.id}:{when.isoformat()}",
                            "configId": None,
                        },
                    )
            activated.append(job.id)
        return activated
