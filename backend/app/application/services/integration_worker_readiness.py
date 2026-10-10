from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.application.services.integration_foundation import (
    IntegrationFoundation,
    IntegrationRequirementError,
)
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIProviderError
from app.domain.identity.permissions import permissions_for_role
from app.domain.identity.principals import HumanPrincipal
from app.domain.integrations.foundation import IntegrationTaskDraft
from app.infrastructure.database.models import (
    IntegrationTaskGrant,
    JobRevision,
    OrganizationMembership,
    Worker,
)


async def reconcile_worker_job(session, gateway, job, revision):
    autonomy = dict(revision.definition.get("autonomy", {}))
    member = await session.scalar(select(OrganizationMembership).where(
        OrganizationMembership.organization_id == job.organization_id,
        OrganizationMembership.user_id == revision.created_by))
    if not member or not {"jobs.manage", "jobs.run"}.issubset(permissions_for_role(member.role)):
        return False
    worker = await session.get(Worker, job.worker_id)
    if worker is None:
        return False
    foundation = IntegrationFoundation(session)
    existing = list((await session.scalars(select(IntegrationTaskGrant).where(
        IntegrationTaskGrant.organization_id == job.organization_id,
        IntegrationTaskGrant.job_revision_id == revision.id,
        IntegrationTaskGrant.worker_id == worker.id,
        IntegrationTaskGrant.active.is_(True)))).all())
    runtime_names = set(foundation.runtime_catalog())
    legacy_runtimes = {need.get("provider") for need in autonomy.get("capabilityNeeds", [])
        if need.get("provider") in runtime_names}
    legacy_runtimes |= set(autonomy.get("missingIntegrations", [])) & runtime_names
    instruction = revision.definition.get("instructions", "")
    if autonomy.get("sourceMessageId"):
        from uuid import UUID

        from app.infrastructure.database.models import ConversationMessage
        source = await session.get(ConversationMessage, UUID(autonomy["sourceMessageId"]))
        if source and source.organization_id == job.organization_id:
            instruction = source.content
    try:
        if settings.smart_planner_enabled and (not autonomy.get("integrationDraft") or legacy_runtimes):
            from app.application.services.preparation_ai import DurablePreparationGateway
            gateway = DurablePreparationGateway(session, job, purpose="integration_worker_preparation", revision=revision)
            await gateway.prepare()
        draft = (IntegrationTaskDraft.model_validate(autonomy["integrationDraft"]) if autonomy.get("integrationDraft") and not legacy_runtimes else
                 await foundation.interpret(gateway, organization_id=job.organization_id,
                    user_id=revision.created_by, instruction=instruction,
                    conversation_context={"runtimeRequirements": sorted(legacy_runtimes)}))
        draft = draft.model_copy(update={"runtime_requirements": sorted(set(draft.runtime_requirements) | legacy_runtimes)})
        await session.refresh(job, with_for_update=True)
        if job.status != "waiting_integration" or job.current_revision != revision.revision:
            return False
        member = await session.scalar(select(OrganizationMembership).where(
            OrganizationMembership.organization_id == job.organization_id,
            OrganizationMembership.user_id == revision.created_by).execution_options(populate_existing=True))
        if not member or not {"jobs.manage", "jobs.run"}.issubset(permissions_for_role(member.role)):
            return False
        bindings = await foundation.resolve(organization_id=job.organization_id, user_id=revision.created_by,
            draft=draft, instruction=instruction)
        if existing and {str(g.resource_id) for g in existing} != {b["id"] for b in bindings}:
            raise IntegrationRequirementError("Recovery resolved different resources. Review the job's exact bindings before granting access.")
        runtime_scopes = {s for runtime in foundation.runtime_catalog().values() for s in runtime["scopes"]}
        for binding in bindings:
            prior = next((g for g in existing if str(g.resource_id) == binding["id"]), None)
            if prior is None:
                continue
            if set(binding["scopes"]) - set(prior.scopes) - runtime_scopes:
                raise IntegrationRequirementError("Recovery needs additional GitHub business capabilities. Review the job's authority before granting them.")
            # Repair known legacy path spelling only while preserving its exact
            # restriction; never let recompilation widen an existing path grant.
            for key, legacy_key in (("allowedPaths", "allowed_paths"), ("deniedPaths", "denied_paths"), ("branches", "branches")):
                old = (prior.constraints or {}).get(key, (prior.constraints or {}).get(legacy_key))
                if old is not None:
                    if key in binding["constraints"] and binding["constraints"][key] != old:
                        raise IntegrationRequirementError("Recovery changed existing path or branch constraints. Review the job's authority.")
                    binding["constraints"][key] = old
    except AIProviderError as error:
        # An inference failure is not a changed job definition. A new revision here
        # would give recovery a new decision/deadline and forget attempted models.
        import logging
        logging.getLogger("uvicorn.error").warning(
            "AI worker preparation paused workload=complex purpose=integration_worker_preparation "
            "job=%s revision=%s category=%s retryable=%s", job.id, revision.id, error.category, error.retryable)
        if error.category == "cancelled":
            return False
        job.status = "waiting_integration"
        return False
    except (IntegrationRequirementError, PermissionError) as error:
        if autonomy.get("integrationWaitReason") != str(error):
            # Waiting explanations also belong to a new revision: old runs must
            # retain the definition and authority they actually executed under.
            job.current_revision += 1
            waiting_revision = JobRevision(id=uuid4(), job_id=job.id, revision=job.current_revision,
                definition={**revision.definition, "autonomy": {**autonomy, "integrationWaitReason": str(error)}},
                created_by=revision.created_by, created_at=datetime.now(UTC))
            session.add(waiting_revision)
            await session.flush()
            for grant in existing:
                session.add(IntegrationTaskGrant(organization_id=job.organization_id,
                    job_revision_id=waiting_revision.id, worker_id=worker.id,
                    initiating_user_id=grant.initiating_user_id, resource_id=grant.resource_id,
                    scopes=list(grant.scopes), destinations=dict(grant.destinations),
                    constraints=dict(grant.constraints or {}), standing=grant.standing,
                    work_item_id=grant.work_item_id, authority_version=grant.authority_version, active=True))
        job.status = "waiting_integration"
        return False
    expected = {(b["id"], tuple(sorted(b["scopes"])), str(sorted(b["destinations"].items())),
        str(sorted(b.get("constraints", {}).items()))) for b in bindings}
    actual = {(str(g.resource_id), tuple(sorted(g.scopes)), str(sorted(g.destinations.items())),
        str(sorted((g.constraints or {}).items()))) for g in existing}
    # Existing grants are not proof of readiness. Revalidate lineage, consent and
    # exact bindings; completed work keeps its old immutable revision and grants.
    from app.infrastructure.database.models import IntegrationConnectionState
    versions_current = True
    for binding in bindings:
        state = await session.scalar(select(IntegrationConnectionState).where(
            IntegrationConnectionState.connection_id == binding["connectionId"]))
        grant = next((g for g in existing if str(g.resource_id) == binding["id"]), None)
        versions_current &= bool(state and grant and state.authority_version == grant.authority_version)
    desired_scopes = {s for b in bindings for s in b["scopes"]}
    current_scopes = {s for s in revision.definition.get("requiredCapabilities", []) if not s.startswith(("browser.", "web."))}
    if expected != actual or not versions_current or desired_scopes != current_scopes or legacy_runtimes or autonomy.get("missingIntegrations"):
        autonomy.update({"integrationDraft": draft.model_dump(mode="json"), "integrationBindings": bindings,
            "integrationWaitReason": None, "missingIntegrations": [],
            "capabilityNeeds": [n for n in autonomy.get("capabilityNeeds", []) if n.get("provider") not in runtime_names]})
        definition = {**revision.definition, "autonomy": autonomy, "integrationRequirements": [],
            "requiredCapabilities": sorted({s for s in revision.definition.get("requiredCapabilities", []) if s.startswith(("browser.", "web."))}
                | {s for b in bindings for s in b["scopes"]})}
        job.current_revision += 1
        revision = JobRevision(id=uuid4(), job_id=job.id, revision=job.current_revision, definition=definition,
            created_by=revision.created_by, created_at=datetime.now(UTC))
        session.add(revision)
        await session.flush()
        await foundation.bind(revision=revision, worker=worker, user_id=revision.created_by, bindings=bindings, standing=True)
    job.status = "active"
    await session.flush()
    if autonomy.get("startWhenReady") and autonomy.get("resultsThreadId"):
        from app.api.jobs_routes import queue_job

        principal = HumanPrincipal(user_id=revision.created_by, organization_id=job.organization_id,
            membership_id=member.id, role=member.role, permissions=permissions_for_role(member.role))
        schedule = autonomy.get("scheduleIntent", {})
        kind = schedule.get("kind", "manual") if isinstance(schedule, dict) else "manual"
        if kind == "manual":
            await queue_job(session, job.organization_id, job, principal)
        elif kind == "once" and schedule.get("once_at"):
            when = datetime.fromisoformat(str(schedule["once_at"]))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            await queue_job(session, job.organization_id, job, principal,
                scheduled_at=max(when.astimezone(UTC), datetime.now(UTC)))
    return True
