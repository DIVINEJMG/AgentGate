from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.application.services.integration_foundation import (
    IntegrationFoundation,
    notify_integration_work,
)
from app.execution.bootstrap import execution_provider_registry
from app.execution.providers.integration_hooks import EventRoutingHooks
from app.infrastructure.database.models import (
    Integration,
    IntegrationConnectionState,
    IntegrationEventInbox,
    IntegrationEventSubscription,
    IntegrationResource,
    IntegrationTaskGrant,
    Job,
    JobRevision,
    WorkItem,
)
from app.infrastructure.database.outbox import TransactionalOutbox


async def accept_event(session, provider_id, event):
    provider = execution_provider_registry().get(provider_id)
    if isinstance(provider, EventRoutingHooks):
        return await provider.accept_event(session=session, event=event)
    states = list((await session.scalars(select(IntegrationConnectionState).join(
        Integration, Integration.id == IntegrationConnectionState.connection_id).where(
        Integration.provider == provider_id, IntegrationConnectionState.account_id == event.account_id))).all())
    if len(states) != 1:
        raise PermissionError("Verified provider account does not resolve to one trusted connection.")
    state = states[0]
    event_id = uuid4()
    inserted = await session.scalar(insert(IntegrationEventInbox).values(id=event_id,
        organization_id=state.organization_id, connection_id=state.connection_id, provider=provider_id,
        delivery_id=event.delivery_id, event_type=event.event_type,
        resource_external_id=event.resource_external_id, evidence=event.evidence,
        status="suppressed" if event.self_generated else "pending").on_conflict_do_nothing(
        constraint="uq_integration_event_delivery").returning(IntegrationEventInbox.id))
    if inserted is None:
        return await session.scalar(select(IntegrationEventInbox.id).where(
            IntegrationEventInbox.provider == provider_id, IntegrationEventInbox.connection_id == state.connection_id,
            IntegrationEventInbox.delivery_id == event.delivery_id))
    if not event.self_generated:
        await TransactionalOutbox(session).enqueue(topic="integration.event.received", aggregate_type="integration_event",
            aggregate_id=str(event_id), payload={"event_id": str(event_id), "organization_id": str(state.organization_id)})
    return event_id


async def process_event(session, event_id):
    event = await session.scalar(select(IntegrationEventInbox).where(IntegrationEventInbox.id == event_id).with_for_update())
    if not event or event.status != "pending":
        return
    provider = execution_provider_registry().get(event.provider)
    if isinstance(provider, EventRoutingHooks) and not await provider.event_allows_matching(session=session, event=event):
        return
    resource = await session.scalar(select(IntegrationResource).where(
        IntegrationResource.connection_id == event.connection_id,
        IntegrationResource.external_id == event.resource_external_id))
    if resource is None:
        event.status = "unmatched"
        return
    subscriptions = (await session.scalars(select(IntegrationEventSubscription).where(
        IntegrationEventSubscription.organization_id == event.organization_id,
        IntegrationEventSubscription.resource_id == resource.id,
        IntegrationEventSubscription.event_type == event.event_type,
        IntegrationEventSubscription.enabled.is_(True)))).all()
    matched = False
    for subscription in subscriptions:
        revision = await session.get(JobRevision, subscription.job_revision_id)
        job = await session.get(Job, revision.job_id)
        if job.status != "active" or job.current_revision != revision.revision:
            continue
        grant = await session.scalar(select(IntegrationTaskGrant).where(
            IntegrationTaskGrant.job_revision_id == revision.id, IntegrationTaskGrant.resource_id == resource.id,
            IntegrationTaskGrant.standing.is_(True), IntegrationTaskGrant.active.is_(True)))
        if not grant or not await IntegrationFoundation(session).can_use(resource.connection_id, event.organization_id, grant.initiating_user_id):
            continue
        key = f"integration-event:{event.id}:{job.id}"
        if await session.scalar(select(WorkItem.id).where(WorkItem.organization_id == event.organization_id, WorkItem.idempotency_key == key)):
            continue
        item = WorkItem(id=uuid4(), organization_id=event.organization_id, job_id=job.id,
            job_revision_id=revision.id, status="queued", priority="normal", correlation_id=str(uuid4()),
            idempotency_key=key, scheduled_at=datetime.now(UTC), payload={
                "integrationOrigin": {"threadId": str(subscription.results_thread_id), "eventId": str(event.id)},
                "requestedBy": str(grant.initiating_user_id), "trigger": {"type": "event",
                    "trust": "untrusted_provider_evidence", "eventId": str(event.id), "payload": event.evidence}})
        session.add(item)
        await session.flush()
        await notify_integration_work(session, item, key="queued", content="A verified provider event matched your configured job. I queued its authorized work.")
        await TransactionalOutbox(session).enqueue(topic="run.progress", aggregate_type="work_item", aggregate_id=str(item.id),
            payload={"organization_id": str(item.organization_id), "work_item_id": str(item.id), "current_step": 0,
                "continuation_phase": "plan", "correlation_id": item.correlation_id})
        matched = True
    event.status = "processed" if matched else "unmatched"
