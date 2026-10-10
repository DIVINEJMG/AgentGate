"""Explicit development-only preparation from existing, currently valid authority."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from scripts.github_scripted_planner import FixtureBlocked, ScriptedPlanner


def fixture_scopes(plan):
    return sorted({"github." + step["operation"] for step in plan["steps"]})


def fixture_binding(resource, grant, plan):
    if resource.provider != "github" or resource.configuration.get("repository", "").casefold() != plan["repository"].casefold():
        raise FixtureBlocked("Source grant does not bind the fixture repository")
    scopes = fixture_scopes(plan)
    missing = set(scopes) - set(grant.scopes)
    if missing:
        raise FixtureBlocked("Source authority lacks: " + ", ".join(sorted(missing)))
    return {"id": str(resource.id), "connectionId": str(resource.connection_id),
            "provider": "github", "name": resource.display_name, "scopes": scopes,
            "destinations": deepcopy(grant.destinations), "constraints": deepcopy(grant.constraints)}


async def prepare(args):
    if not args.source_work_item_id or not args.organization_id or not args.preparation_key or not args.workers_stopped:
        raise FixtureBlocked("Preparation requires organization/source-work-item IDs, --preparation-key and --workers-stopped")
    from sqlalchemy import select, text

    from app.application.services.integration_foundation import IntegrationFoundation
    from app.domain.identity.permissions import permissions_for_role
    from app.infrastructure.database import session as database
    from app.infrastructure.database.models import (
        ConversationMessage,
        ConversationThread,
        IntegrationResource,
        IntegrationTaskGrant,
        Job,
        JobRevision,
        OrganizationMembership,
        Worker,
        WorkItem,
    )

    # Never dispatch normal planning from this CLI. No run.progress event is created.
    database.request_outbox_drain_after_commit = lambda **kwargs: None
    org, source_id = UUID(args.organization_id), UUID(args.source_work_item_id)
    if len(args.preparation_key) > 80:
        raise FixtureBlocked("Preparation key must be at most 80 characters")
    key = f"scripted-prepare:{source_id}:{args.preparation_key}"
    async with database.session_factory() as session:
        # Serialize repeated invocations before creating the job, messages or grants.
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                              {"key": str(org) + ":" + key})
        existing = await session.scalar(select(WorkItem).where(
            WorkItem.organization_id == org, WorkItem.idempotency_key == key))
        if existing:
            planner = ScriptedPlanner(args.manifest, str(existing.payload["scriptedResourceId"]), str(existing.id), args.publish)
            if existing.payload.get("scriptedPlannerFixture") != planner.fingerprint:
                raise FixtureBlocked("Preparation key already belongs to a different fixture/publication choice")
            result = {"workItemId": str(existing.id), "resourceId": existing.payload["scriptedResourceId"],
                      "status": existing.status, "reused": True}
            await session.rollback()
            return result
        source = await session.get(WorkItem, source_id)
        if not source or source.organization_id != org:
            raise FixtureBlocked("Source work item is unavailable in this organization")
        revision = await session.get(JobRevision, source.job_revision_id)
        job = await session.get(Job, source.job_id)
        if not revision or not job or job.organization_id != org or revision.job_id != job.id:
            raise FixtureBlocked("Invalid source job lineage")
        worker = await session.get(Worker, job.worker_id)
        if not worker or worker.organization_id != org or not worker.agent_identity_id:
            raise FixtureBlocked("Source worker is unavailable")
        actor = UUID(str(source.payload.get("requestedBy", revision.created_by)))
        membership = await session.scalar(select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org, OrganizationMembership.user_id == actor))
        if not membership or not {"jobs.run", "jobs.manage"}.issubset(permissions_for_role(membership.role)):
            raise FixtureBlocked("Source initiating user cannot create and run jobs")
        item_id = uuid4()
        probe = ScriptedPlanner(args.manifest, "unresolved", str(item_id), args.publish)
        grants = (await session.scalars(select(IntegrationTaskGrant).where(
            IntegrationTaskGrant.organization_id == org, IntegrationTaskGrant.job_revision_id == revision.id,
            IntegrationTaskGrant.worker_id == worker.id, IntegrationTaskGrant.active.is_(True)))).all()
        candidates = []
        for grant in grants:
            resource = await session.get(IntegrationResource, grant.resource_id)
            if resource and resource.organization_id == org and resource.provider == "github" and resource.configuration.get("repository", "").casefold() == probe.plan["repository"].casefold():
                candidates.append((grant, resource))
        if len(candidates) != 1:
            raise FixtureBlocked("Source must have exactly one active grant for the fixture repository")
        grant, resource = candidates[0]
        foundation = IntegrationFoundation(session)
        # Recheck sharing, membership, consent/version and resource availability; no copied stale access.
        await foundation.authorize(organization_id=org, agent_id=worker.agent_identity_id,
            work_item_id=source.id, resource_id=resource.id, scope=None, payload={})
        binding = fixture_binding(resource, grant, probe.plan)
        available = await foundation.resource_capabilities(resource)
        missing = set(binding["scopes"]) - set(available)
        if missing:
            raise FixtureBlocked("Currently unavailable capabilities: " + ", ".join(sorted(missing)))
        origin = source.payload.get("integrationOrigin", {})
        thread = await session.get(ConversationThread, UUID(origin["threadId"])) if origin.get("threadId") else None
        if not thread or thread.organization_id != org or thread.worker_id != worker.id or thread.created_by != actor:
            raise FixtureBlocked("Source conversation is unavailable to the initiating user/worker")
        now = datetime.now(UTC)
        new_job = Job(id=uuid4(), organization_id=org, worker_id=worker.id,
                      name="Controlled scripted GitHub verification", status="active", current_revision=1)
        message = ConversationMessage(id=uuid4(), organization_id=org, thread_id=thread.id, role="human",
            content="Controlled scripted verification of the saved timezone-check request. Supplied decisions replace AI inference; existing authority and publication checks still apply.",
            command_references=[{"type": "work_item", "id": str(item_id)}])
        definition = deepcopy(revision.definition)
        definition["requiredCapabilities"] = binding["scopes"]
        definition["schedule"] = {"kind": "manual"}
        definition.pop("triggerConfig", None)
        definition["autonomy"] = {**definition.get("autonomy", {}), "integrationFoundation": True,
            "integrationBindings": [binding], "oneShot": True, "startWhenReady": False,
            "sourceThreadId": str(thread.id),
            "sourceMessageId": str(message.id), "resultsThreadId": str(thread.id)}
        new_revision = JobRevision(id=uuid4(), job_id=new_job.id, revision=1, created_by=actor,
                                  created_at=now, definition=definition)
        planner = ScriptedPlanner(args.manifest, str(resource.id), str(item_id), args.publish)
        item = WorkItem(id=item_id, organization_id=org, job_id=new_job.id, job_revision_id=new_revision.id,
            status="queued", priority="normal", correlation_id=str(uuid4()), idempotency_key=key, scheduled_at=now,
            payload={"requestedBy": str(actor), "trigger": {"type": "manual"},
                "integrationOrigin": {"threadId": str(thread.id), "messageId": str(message.id)},
                "scriptedPreparationSource": str(source.id), "scriptedResourceId": str(resource.id),
                "scriptedPlannerFixture": planner.fingerprint})
        session.add_all([new_job, message])
        await session.flush()
        session.add(new_revision)
        await session.flush()
        session.add(item)
        await session.flush()
        await foundation.bind(revision=new_revision, worker=worker, user_id=actor, bindings=[binding],
                              standing=False, work_item_id=item.id)
        await session.commit()
        return {"workItemId": str(item.id), "resourceId": str(resource.id), "status": item.status,
                "reused": False, "inferenceCalls": 0, "executionDispatched": False}
