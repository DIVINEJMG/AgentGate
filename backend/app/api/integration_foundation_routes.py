from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import append_audit, require_permission
from app.application.services.integration_credentials import (
    CredentialManager,
    decode_bundle,
    encode_bundle,
)
from app.application.services.integration_foundation import IntegrationFoundation
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.execution.bootstrap import execution_provider_registry
from app.execution.providers.integration_hooks import AuthenticationHooks, EventHooks
from app.infrastructure.database.models import (
    ConversationCommand,
    ConversationThread,
    Integration,
    IntegrationAccessGrant,
    IntegrationAuthState,
    IntegrationConnectionState,
    IntegrationCredential,
    IntegrationEventSubscription,
    IntegrationResource,
    IntegrationTaskGrant,
    Job,
    JobRevision,
    OrganizationMembership,
    Worker,
    WorkItem,
)
from app.infrastructure.database.outbox import TransactionalOutbox
from app.infrastructure.database.session import database_session
from app.infrastructure.secrets.integration_crypto import (
    decrypt_integration_secret,
    encrypt_integration_secret,
)

router = APIRouter(tags=["integration-foundation"])
event_router = APIRouter(tags=["integration-events"])
Principal = Annotated[HumanPrincipal, Depends(organization_principal)]
Session = Annotated[AsyncSession, Depends(database_session)]


def enabled():
    if not settings.integration_foundation_enabled:
        raise HTTPException(404, "Integration foundation is not enabled.")


async def owned(session, organization_id, connection_id, principal):
    enabled()
    state = await session.scalar(select(IntegrationConnectionState).where(
        IntegrationConnectionState.organization_id == organization_id,
        IntegrationConnectionState.connection_id == connection_id).with_for_update())
    if not state or state.owner_id != principal.user_id or state.ownership_state != "owned":
        raise HTTPException(403, "Only the connection owner may change sharing or credentials.")
    return state


@router.get("/organizations/{organization_id}/integration-foundation")
async def readiness(organization_id: UUID, principal: Principal):
    require_permission(principal, "integrations.read")
    return {"enabled": settings.integration_foundation_enabled,
        "providers": [{"provider": p.manifest.provider,
            "authenticationAvailable": isinstance(p, AuthenticationHooks),
            "eventIntakeAvailable": isinstance(p, EventHooks)}
            for p in execution_provider_registry().providers(kind="native_api")]}


@router.get("/organizations/{organization_id}/integration-connections")
async def connections(organization_id: UUID, principal: Principal, session: Session):
    enabled()
    require_permission(principal, "integrations.read")
    rows = (await session.scalars(select(IntegrationConnectionState).where(
        IntegrationConnectionState.organization_id == organization_id))).all()
    result = []
    foundation = IntegrationFoundation(session)
    for state in rows:
        if not await foundation.can_use(state.connection_id, organization_id, principal.user_id):
            continue
        connection = await session.get(Integration, state.connection_id)
        if connection is None:
            continue
        result.append({"id": str(state.connection_id), "ownerId": str(state.owner_id),
            "provider": connection.provider, "displayName": connection.display_name,
            "authorizationState": state.authorization_state, "health": connection.status,
            "reason": state.reason, "authorityVersion": state.authority_version,
            "canShare": state.owner_id == principal.user_id})
        result[-1]["authenticationStrategy"] = "github_app" if connection.provider == "github" and connection.config.get("installationId") else "static"
    return {"connections": result}


@router.get("/organizations/{organization_id}/integration-resources")
async def resources(organization_id: UUID, principal: Principal, session: Session):
    enabled()
    require_permission(principal, "integrations.read")
    return {"resources": await IntegrationFoundation(session).catalog(organization_id, principal.user_id)}


class ShareInput(BaseModel):
    subject_type: str = Field(pattern="^(user|worker)$")
    subject_id: UUID
    active: bool = True


@router.get("/organizations/{organization_id}/integration-connections/{connection_id}/sharing")
async def sharing(organization_id: UUID, connection_id: UUID, principal: Principal, session: Session):
    await owned(session, organization_id, connection_id, principal)
    from app.infrastructure.database.models import HumanIdentity
    members = (await session.scalars(select(HumanIdentity).join(OrganizationMembership,
        OrganizationMembership.user_id == HumanIdentity.id).where(OrganizationMembership.organization_id == organization_id))).all()
    workers = (await session.scalars(select(Worker).where(Worker.organization_id == organization_id))).all()
    grants = (await session.scalars(select(IntegrationAccessGrant).where(
        IntegrationAccessGrant.organization_id == organization_id, IntegrationAccessGrant.connection_id == connection_id))).all()
    return {"subjects": [{"id": str(m.id), "type": "user", "name": m.display_name or m.email or "Organization member"}
        for m in members if m.id != principal.user_id] + [{"id": str(w.id), "type": "worker", "name": w.name} for w in workers],
        "grants": [{"subjectId": str(g.subject_id), "subjectType": g.subject_type, "active": g.active} for g in grants]}


@router.post("/organizations/{organization_id}/integration-connections/{connection_id}/sharing")
async def share(organization_id: UUID, connection_id: UUID, body: ShareInput, principal: Principal, session: Session):
    state = await owned(session, organization_id, connection_id, principal)
    target = (await session.scalar(select(OrganizationMembership).where(
        OrganizationMembership.organization_id == organization_id,
        OrganizationMembership.user_id == body.subject_id)) if body.subject_type == "user" else
        await session.scalar(select(Worker).where(Worker.organization_id == organization_id, Worker.id == body.subject_id)))
    if target is None:
        raise HTTPException(404, "Share recipient is outside this organization.")
    row = await session.scalar(select(IntegrationAccessGrant).where(
        IntegrationAccessGrant.connection_id == connection_id,
        IntegrationAccessGrant.subject_type == body.subject_type, IntegrationAccessGrant.subject_id == body.subject_id))
    if row is None:
        row = IntegrationAccessGrant(organization_id=organization_id, connection_id=connection_id,
            subject_type=body.subject_type, subject_id=body.subject_id, granted_by=principal.user_id)
        session.add(row)
    row.active = body.active
    # Revalidation reads this grant on every action; changing another member's
    # share must not invalidate an unrelated owner's pending job.
    await append_audit(session, organization_id=organization_id, principal=principal,
        event_type="integration.sharing.changed", category="security", resource_type="integration",
        resource_id=str(connection_id), resource_name="Integration account", outcome="shared" if body.active else "revoked",
        summary="The connection owner changed explicit account access.",
        metadata={"subjectType": body.subject_type, "subjectId": str(body.subject_id)})
    if body.active:
        await IntegrationFoundation(session).queue_waiting_preparation(organization_id,
            body.subject_id if body.subject_type == "user" else principal.user_id)
    await session.commit()
    return {"shared": body.active, "authorityVersion": state.authority_version}


@router.post("/organizations/{organization_id}/integration-connections/{connection_id}/discover")
async def discover(organization_id: UUID, connection_id: UUID, principal: Principal, session: Session):
    enabled()
    if not await IntegrationFoundation(session).can_use(connection_id, organization_id, principal.user_id):
        raise HTTPException(403, "This account has not been shared with you.")
    connection = await session.get(Integration, connection_id)
    if connection is None:
        raise HTTPException(404, "Connection not found.")
    provider = execution_provider_registry().get(connection.provider)
    credential = await CredentialManager(session).resolve(connection=connection, provider=provider, correlation_id="resource-discovery")
    rows = await IntegrationFoundation(session).discover(connection, credential)
    await IntegrationFoundation(session).queue_waiting_preparation(organization_id, principal.user_id)
    await session.commit()
    return {"count": len(rows)}


class ReconnectInput(BaseModel):
    credential: str = Field(min_length=1, repr=False)


@router.post("/organizations/{organization_id}/integration-connections/{connection_id}/reconnect")
async def reconnect(organization_id: UUID, connection_id: UUID, body: ReconnectInput, principal: Principal, session: Session):
    state = await owned(session, organization_id, connection_id, principal)
    connection = await session.get(Integration, connection_id)
    if connection is None:
        raise HTTPException(404, "Connection not found.")
    if connection.provider == "github" and connection.config.get("installationId"):
        raise HTTPException(409, "Reconnect this account through verified GitHub App authorization, not a replacement token.")
    provider = execution_provider_registry().get(connection.provider)
    config = {str(k): str(v) for k, v in connection.config.items() if isinstance(v, (str, int, bool))}
    health = await provider.check_health(configuration=config, credential=body.credential)
    if health.state != "healthy":
        raise HTTPException(409, "The provider could not validate this account. The pending checkpoint is preserved.")
    discovered = await provider.discover_resources(configuration=config, credential=body.credential)
    expected_identity = str(connection.config.get("resourceKey") or connection.id)
    if not any(r.external_id == expected_identity for r in discovered):
        raise HTTPException(409, "This credential resolves to a different account/resource. Connect it separately instead of replacing this account.")
    row = await session.scalar(select(IntegrationCredential).where(IntegrationCredential.integration_id == connection_id).with_for_update())
    if row is None:
        row = IntegrationCredential(integration_id=connection_id, key_version=1)
        session.add(row)
    row.ciphertext = encrypt_integration_secret(body.credential)
    state.authorization_state, state.reason = "connected", ""
    connection.status = "connected"
    await append_audit(session, organization_id=organization_id, principal=principal,
        event_type="integration.reconnected", category="integration", resource_type="integration",
        resource_id=str(connection_id), resource_name=connection.display_name, outcome="connected",
        summary="The owner validated and replaced the encrypted provider credential.")
    await session.commit()
    return {"authorizationState": "connected", "message": "Reconnected. Resume pending tasks to revalidate their exact action."}


class ResumeInput(BaseModel):
    clarification: str = Field(default="", max_length=6000)


class EventSubscriptionInput(BaseModel):
    resource_id: UUID
    event_type: str = Field(min_length=1, max_length=128)
    results_thread_id: UUID
    enabled: bool = True


@router.post("/organizations/{organization_id}/integration-jobs/{job_id}/events")
async def subscribe_event(organization_id: UUID, job_id: UUID, body: EventSubscriptionInput, principal: Principal, session: Session):
    enabled()
    require_permission(principal, "jobs.manage")
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.organization_id == organization_id))
    thread = await session.scalar(select(ConversationThread).where(ConversationThread.id == body.results_thread_id,
        ConversationThread.organization_id == organization_id, ConversationThread.created_by == principal.user_id))
    if not job or not thread or thread.worker_id != job.worker_id:
        raise HTTPException(404, "The job and results conversation must belong to the same worker and organization.")
    revision = await session.scalar(select(JobRevision).where(JobRevision.job_id == job.id, JobRevision.revision == job.current_revision))
    if revision is None:
        raise HTTPException(404, "Job revision not found.")
    grant = await session.scalar(select(IntegrationTaskGrant).where(IntegrationTaskGrant.job_revision_id == revision.id,
        IntegrationTaskGrant.resource_id == body.resource_id, IntegrationTaskGrant.initiating_user_id == principal.user_id,
        IntegrationTaskGrant.standing.is_(True), IntegrationTaskGrant.active.is_(True)))
    if not grant:
        raise HTTPException(403, "Event jobs require the human's standing authority for this exact resource.")
    row = await session.scalar(select(IntegrationEventSubscription).where(
        IntegrationEventSubscription.job_revision_id == revision.id, IntegrationEventSubscription.resource_id == body.resource_id,
        IntegrationEventSubscription.event_type == body.event_type))
    if row is None:
        row = IntegrationEventSubscription(organization_id=organization_id, job_revision_id=revision.id,
            resource_id=body.resource_id, event_type=body.event_type, results_thread_id=thread.id)
        session.add(row)
    row.enabled, row.results_thread_id = body.enabled, thread.id
    await session.commit()
    return {"enabled": row.enabled}


@router.post("/organizations/{organization_id}/integration-tasks/{command_id}/prepare")
async def resume_preparation(organization_id: UUID, command_id: UUID, body: ResumeInput, principal: Principal, session: Session):
    enabled()
    command = await session.scalar(select(ConversationCommand).where(ConversationCommand.id == command_id,
        ConversationCommand.organization_id == organization_id).with_for_update())
    if not command or command.created_by != principal.user_id or command.family != "integration.execute":
        raise HTTPException(404, "Your integration task was not found.")
    if command.target_id:
        return {"status": "accepted", "workItemId": command.target_id}
    if body.clarification:
        task = dict(command.payload["integrationTask"])
        task["instruction"] += "\nHuman clarification: " + body.clarification
        task.pop("draft", None)
        command.payload = {**command.payload, "integrationTask": task}
    command.status = "accepted"
    from app.application.services.preparation_ai import restart_exhausted_preparation
    retry_state = await restart_exhausted_preparation(session, command)
    await TransactionalOutbox(session).enqueue(topic="integration.task.prepare", aggregate_type="conversation_command",
        aggregate_id=str(command.id), payload={"organization_id": str(organization_id), "command_id": str(command.id)})
    await session.commit()
    return {"status": "accepted", "preparationState": retry_state}


@router.get("/organizations/{organization_id}/integration-work/{work_item_id}")
async def work_status(organization_id: UUID, work_item_id: UUID, principal: Principal, session: Session):
    enabled()
    require_permission(principal, "jobs.read")
    item = await session.get(WorkItem, work_item_id)
    if not item or item.organization_id != organization_id:
        raise HTTPException(404, "Work item not found.")
    return {"status": item.status, "runtime": (item.payload or {}).get("runtime", {}),
        "origin": (item.payload or {}).get("integrationOrigin"), "revisionId": str(item.job_revision_id)}


@router.post("/organizations/{organization_id}/integration-work/{work_item_id}/resume")
async def resume_work(organization_id: UUID, work_item_id: UUID, principal: Principal, session: Session):
    enabled()
    require_permission(principal, "jobs.run")
    item = await session.scalar(select(WorkItem).where(WorkItem.id == work_item_id,
        WorkItem.organization_id == organization_id).with_for_update())
    if not item or (item.payload or {}).get("requestedBy") != str(principal.user_id):
        raise HTTPException(404, "Your pending task was not found.")
    if item.status != "waiting_reconnect":
        raise HTTPException(409, "Only reconnect waits can resume here. An uncertain write must be reconciled before replay.")
    runtime = dict(item.payload.get("runtime", {}))
    # The human explicitly resumed this task. Refresh existing exact grants only
    # after shared access, resource health and current provider consent pass.
    grants = (await session.scalars(select(IntegrationTaskGrant).where(
        IntegrationTaskGrant.job_revision_id == item.job_revision_id,
        IntegrationTaskGrant.initiating_user_id == principal.user_id))).all()
    for grant in grants:
        resource = await session.get(IntegrationResource, grant.resource_id)
        if not resource or resource.health != "healthy" or not await IntegrationFoundation(session).can_use(resource.connection_id, organization_id, principal.user_id, grant.worker_id):
            raise HTTPException(409, "An exact resource binding still requires access review.")
        state = await session.scalar(select(IntegrationConnectionState).where(IntegrationConnectionState.connection_id == resource.connection_id))
        if not state or state.authorization_state != "connected" or not set(grant.scopes).issubset(resource.capabilities):
            raise HTTPException(409, "Provider permissions no longer cover this task. Review its requested authority.")
        grant.authority_version, grant.active = state.authority_version, True
    item.status, item.scheduled_at = "queued", datetime.now(UTC)
    await TransactionalOutbox(session).enqueue(topic="run.progress", aggregate_type="work_item", aggregate_id=str(item.id),
        payload={"organization_id": str(item.organization_id), "work_item_id": str(item.id),
            "current_step": int(runtime.get("currentStep", 0)), "continuation_phase": "execute"})
    await session.commit()
    return {"status": "queued", "message": "The pending action will revalidate access and provider consent before execution."}


@router.get("/organizations/{organization_id}/integration-review")
async def review_legacy(organization_id: UUID, principal: Principal, session: Session):
    enabled()
    require_permission(principal, "integrations.manage")
    from app.infrastructure.database.models import CapabilityProfile
    states = (await session.scalars(select(IntegrationConnectionState).where(
        IntegrationConnectionState.organization_id == organization_id,
        IntegrationConnectionState.ownership_state == "review_required"))).all()
    scopes = (await session.scalars(select(CapabilityProfile).where(CapabilityProfile.organization_id == organization_id,
        CapabilityProfile.active.is_(True)))).all()
    return {"ownershipReview": [{"connectionId": str(s.connection_id), "reason": s.reason} for s in states],
        "legacyScopeReview": [{"agentId": str(s.agent_id), "scope": s.scope} for s in scopes
            if not s.scope.startswith(("browser.", "web."))],
        "message": "Scope declarations confer no access to newly discovered resources. Bind exact resources before adoption."}


class OwnerReviewInput(BaseModel):
    owner_id: UUID


@router.post("/organizations/{organization_id}/integration-connections/{connection_id}/owner-review")
async def review_owner(organization_id: UUID, connection_id: UUID, body: OwnerReviewInput, principal: Principal, session: Session):
    enabled()
    require_permission(principal, "memberships.manage")
    member = await session.scalar(select(OrganizationMembership).where(OrganizationMembership.organization_id == organization_id,
        OrganizationMembership.user_id == body.owner_id))
    state = await session.scalar(select(IntegrationConnectionState).where(IntegrationConnectionState.organization_id == organization_id,
        IntegrationConnectionState.connection_id == connection_id).with_for_update())
    if not member or not state or state.ownership_state != "review_required":
        raise HTTPException(409, "Ownership review requires an unresolved connection and a current member.")
    state.owner_id, state.ownership_state, state.reason = body.owner_id, "owned", ""
    state.authority_version += 1
    await append_audit(session, organization_id=organization_id, principal=principal,
        event_type="integration.owner.reviewed", category="security", resource_type="integration",
        resource_id=str(connection_id), resource_name="Integration account", outcome="owned",
        summary="An administrator resolved legacy connection ownership.", metadata={"ownerId": str(body.owner_id)})
    await session.commit()
    return {"ownerId": str(state.owner_id), "ownershipState": "owned"}


@router.post("/organizations/{organization_id}/integration-auth/{provider_id}/initiate")
async def initiate_auth(organization_id: UUID, provider_id: str, principal: Principal, session: Session):
    enabled()
    require_permission(principal, "integrations.manage")
    try:
        provider = execution_provider_registry().get(provider_id)
    except KeyError:
        raise HTTPException(404, "Unknown provider.") from None
    if not isinstance(provider, AuthenticationHooks) or not settings.integration_auth_callback_base_url.startswith("https://"):
        raise HTTPException(409, "Provider authentication is not implemented or its HTTPS callback is unconfigured.")
    nonce = secrets.token_urlsafe(32)
    callback = settings.integration_auth_callback_base_url.rstrip("/") + f"/api/v1/organizations/{organization_id}/integration-auth/{provider_id}/callback"
    session.add(IntegrationAuthState(organization_id=organization_id, owner_id=principal.user_id,
        provider=provider_id, state_hash=hashlib.sha256(nonce.encode()).hexdigest(), callback_url=callback,
        expires_at=datetime.now(UTC) + timedelta(minutes=10), consumed=False))
    url = await provider.initiate_authorization(state=nonce, callback_url=callback)
    await session.commit()
    return {"authorizationUrl": url}


class AuthCallbackInput(BaseModel):
    state: str = Field(repr=False)
    code: str = Field(repr=False)
    connection_id: UUID


@router.post("/organizations/{organization_id}/integration-auth/{provider_id}/callback")
async def complete_auth(organization_id: UUID, provider_id: str, body: AuthCallbackInput, principal: Principal, session: Session):
    state = await owned(session, organization_id, body.connection_id, principal)
    auth = await session.scalar(select(IntegrationAuthState).where(
        IntegrationAuthState.state_hash == hashlib.sha256(body.state.encode()).hexdigest()).with_for_update())
    if not auth or auth.consumed or auth.owner_id != principal.user_id or auth.organization_id != organization_id or auth.provider != provider_id or auth.expires_at <= datetime.now(UTC):
        raise HTTPException(400, "Invalid or expired authorization state.")
    provider = execution_provider_registry().get(provider_id)
    connection = await session.get(Integration, body.connection_id)
    if connection is None:
        raise HTTPException(404, "Connection not found.")
    if not isinstance(provider, AuthenticationHooks) or connection.provider != provider_id:
        raise HTTPException(409, "Provider authentication is unavailable for this connection.")
    bundle = await provider.complete_authorization(code=body.code, callback_url=auth.callback_url)
    if state.account_id and bundle.account_id != state.account_id:
        raise HTTPException(409, "Authorization returned a different account; create a separate connection instead.")
    row = await session.scalar(select(IntegrationCredential).where(IntegrationCredential.integration_id == connection.id).with_for_update())
    if row is None:
        row = IntegrationCredential(integration_id=connection.id, key_version=1)
        session.add(row)
    row.ciphertext = encrypt_integration_secret(encode_bundle(bundle))
    auth.consumed = True
    state.authorization_state, state.reason = "connected", ""
    state.account_id = bundle.account_id
    state.credential_metadata = {"scopes": list(bundle.scopes), "expiresAt": bundle.expires_at.isoformat() if bundle.expires_at else None}
    await session.commit()
    return {"authorizationState": "connected"}


@router.post("/organizations/{organization_id}/integration-connections/{connection_id}/revoke")
async def revoke_auth(organization_id: UUID, connection_id: UUID, principal: Principal, session: Session):
    state = await owned(session, organization_id, connection_id, principal)
    connection = await session.get(Integration, connection_id)
    if connection is None:
        raise HTTPException(404, "Connection not found.")
    provider = execution_provider_registry().get(connection.provider)
    if not isinstance(provider, AuthenticationHooks):
        raise HTTPException(409, "Provider credential revocation is not implemented.")
    row = await session.scalar(select(IntegrationCredential).where(
        IntegrationCredential.integration_id == connection_id).with_for_update())
    if row is None:
        raise HTTPException(409, "This account has no stored credential to revoke.")
    await provider.revoke_credentials(bundle=decode_bundle(decrypt_integration_secret(row.ciphertext)))
    await session.delete(row)
    state.authorization_state, state.reason = "disconnected", "The owner revoked this account credential."
    state.authority_version += 1
    await append_audit(session, organization_id=organization_id, principal=principal,
        event_type="integration.credential.revoked", category="security", resource_type="integration",
        resource_id=str(connection_id), resource_name="Integration account", outcome="disconnected",
        summary="The owner revoked provider access.", metadata={})
    await session.commit()
    return {"authorizationState": "disconnected"}


@event_router.post("/integration-events/{provider_id}")
async def incoming_event(provider_id: str, request: Request, session: Session):
    enabled()
    try:
        provider = execution_provider_registry().get(provider_id)
    except KeyError:
        raise HTTPException(404, "Unknown provider.") from None
    if not isinstance(provider, EventHooks):
        raise HTTPException(409, "Verified event intake is unavailable for this provider.")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 256_000:
            raise HTTPException(413, "Provider event exceeds intake size budget.")
    try:
        event = await provider.verify_and_normalize_event(headers=dict(request.headers), body=bytes(chunks))
    except (ValueError, PermissionError):
        raise HTTPException(401, "Provider event authenticity could not be verified.") from None
    from app.application.services.integration_events import accept_event
    try:
        event_id = await accept_event(session, provider_id, event)
    except PermissionError:
        raise HTTPException(401, "Provider event account is not a trusted connection.") from None
    await session.commit()
    return {"accepted": True, "eventId": str(event_id)}
