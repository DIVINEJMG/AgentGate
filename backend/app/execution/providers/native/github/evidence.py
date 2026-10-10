"""GitHub write provenance, kept outside the provider-neutral runtime."""

from uuid import UUID

from sqlalchemy import select

from app.domain.integrations.foundation import payload_fingerprint
from app.execution.redaction import redacted_dict
from app.infrastructure.database.models import GitHubActionEvidence


async def record_dispatch(session, request):
    connection = UUID(str(request.resource.metadata["connectionId"]))
    existing = await session.scalar(
        select(GitHubActionEvidence).where(
            GitHubActionEvidence.connection_id == connection,
            GitHubActionEvidence.action_key == request.idempotency_key,
        )
    )
    fingerprint = payload_fingerprint(
        request.resource.id,
        request.capability.scope,
        request.input,
        int(str(request.resource.metadata["authorityVersion"])),
    )
    if existing is None:
        session.add(
            GitHubActionEvidence(
                organization_id=request.organization_id,
                connection_id=connection,
                work_item_id=request.work_item_id,
                action_key=request.idempotency_key,
                operation=request.operation,
                resource_external_id=request.resource.external_id,
                payload_fingerprint=fingerprint,
                evidence={"state": "dispatching"},
            )
        )
    elif existing.payload_fingerprint != fingerprint:
        raise PermissionError(
            "A durable GitHub action cannot be reused with changed content or authority."
        )


async def record_outcome(session, request, verification):
    if not request.capability.side_effect:
        return
    row = await session.scalar(
        select(GitHubActionEvidence)
        .where(
            GitHubActionEvidence.connection_id
            == UUID(str(request.resource.metadata["connectionId"])),
            GitHubActionEvidence.action_key == request.idempotency_key,
        )
        .with_for_update()
    )
    if row:
        row.external_id = str(verification.details.get("externalId") or "") or None
        row.evidence = {
            "state": "verified" if verification.verified else "uncertain",
            **redacted_dict(verification.details),
        }
