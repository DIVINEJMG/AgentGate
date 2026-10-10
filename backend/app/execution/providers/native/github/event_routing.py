"""Installation routing and write provenance never infer cross-tenant authority."""

from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database.models import (
    IntegrationConnectionState,
    IntegrationEventInbox,
    IntegrationResource,
    IntegrationTaskGrant,
)
from app.infrastructure.database.outbox import TransactionalOutbox


async def accept_github_event(session, event):
    from app.infrastructure.database.models import GitHubInstallationBinding

    bindings = (
        await session.scalars(
            select(GitHubInstallationBinding).where(
                GitHubInstallationBinding.installation_id == event.evidence["installationId"]
            )
        )
    ).all()
    if not bindings:
        raise PermissionError("Installation has no explicitly verified tenant connection binding.")
    result = None
    for binding in bindings:
        identity = uuid4()
        inserted = await session.scalar(
            insert(IntegrationEventInbox)
            .values(
                id=identity,
                organization_id=binding.organization_id,
                connection_id=binding.connection_id,
                provider="github",
                delivery_id=event.delivery_id,
                event_type=event.event_type,
                resource_external_id=event.evidence.get("projectId") or event.resource_external_id,
                evidence=event.evidence,
                status="pending",
            )
            .on_conflict_do_nothing(constraint="uq_integration_event_delivery")
            .returning(IntegrationEventInbox.id)
        )
        if inserted is not None:
            await TransactionalOutbox(session).enqueue(
                topic="integration.event.received",
                aggregate_type="integration_event",
                aggregate_id=str(identity),
                payload={
                    "event_id": str(identity),
                    "organization_id": str(binding.organization_id),
                    "provider": "github",
                },
            )
        result = inserted or result
    return result or event.delivery_id


async def github_event_state(session, event):
    """Installation state changes and exact write correlation precede job matching."""
    import hashlib

    from app.bootstrap.settings import settings
    from app.infrastructure.database.models import (
        GitHubActionEvidence,
        GitHubInstallationBinding,
        Integration,
    )

    binding = await session.scalar(
        select(GitHubInstallationBinding)
        .where(GitHubInstallationBinding.connection_id == event.connection_id)
        .with_for_update()
    )
    if not binding:
        event.status = "unmatched"
        return False
    state = await session.scalar(
        select(IntegrationConnectionState)
        .where(IntegrationConnectionState.connection_id == binding.connection_id)
        .with_for_update()
    )
    if event.event_type in {"installation", "installation_repositories"}:
        action = event.evidence.get("action")
        # Reordered events must not restore revoked access. Unsuspend/addition
        # requires fresh authenticated discovery, not a webhook-based grant.
        reason = "GitHub installation access changed; rediscover and review affected task bindings."
        if action in {"deleted", "suspend", "new_permissions_accepted"}:
            binding.status = "suspended" if action == "suspend" else "review_required"
            state.authorization_state, state.reason = "reconnect_required", reason
            state.authority_version += 1
        removed = event.evidence.get("removedRepositoryIds", [])
        if removed:
            resources = (
                await session.scalars(
                    select(IntegrationResource).where(
                        IntegrationResource.connection_id == binding.connection_id,
                        IntegrationResource.external_id.in_(removed),
                    )
                )
            ).all()
            for resource in resources:
                resource.health = "unavailable"
                grants = (
                    await session.scalars(
                        select(IntegrationTaskGrant).where(
                            IntegrationTaskGrant.resource_id == resource.id
                        )
                    )
                ).all()
                for grant in grants:
                    grant.active = False
        event.status = "processed"
        return False
    evidence = (
        await session.scalars(
            select(GitHubActionEvidence)
            .where(
                GitHubActionEvidence.connection_id == event.connection_id,
                GitHubActionEvidence.resource_external_id == event.resource_external_id,
                or_(
                    GitHubActionEvidence.external_id == event.evidence.get("objectId"),
                    GitHubActionEvidence.external_id.is_(None),
                ),
            )
            .order_by(GitHubActionEvidence.created_at.desc())
            .limit(100)
        )
    ).all()
    connection = await session.get(Integration, event.connection_id)
    bot = (
        (connection.config or {}).get("botLogin", settings.github_app_slug + "[bot]")
        if connection
        else ""
    )
    body_digest = hashlib.sha256(event.evidence.get("body", "").encode()).hexdigest()

    def correlated(row):
        key = hashlib.sha256(row.action_key.encode()).hexdigest()
        return (
            "<!-- audoryn-action:" + key + " -->" in event.evidence.get("body", "")
            or row.evidence.get("bodyDigest") == body_digest
            or event.evidence.get("externalActionId") == "<!-- audoryn-action:" + key + " -->"
            or (
                event.event_type == "push"
                and any(
                    "Audoryn-Action: " + key in m for m in event.evidence.get("commitMessages", [])
                )
            )
        )

    if event.evidence.get("senderLogin") == bot and any(correlated(row) for row in evidence):
        event.status = "suppressed"
        return False
    return binding.status == "active"
