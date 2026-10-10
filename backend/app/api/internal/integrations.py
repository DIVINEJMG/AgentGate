from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.application.services.integration_events import process_event
from app.application.services.integration_tasks import prepare_integration_task
from app.application.services.integration_worker_readiness import reconcile_worker_job
from app.bootstrap.settings import settings
from app.infrastructure.ai.provider import ai_gateway_from_settings
from app.infrastructure.database.models import (
    ConversationCommand,
    IntegrationEventInbox,
    Job,
    JobRevision,
)
from app.infrastructure.database.session import session_factory
from app.infrastructure.qstash.verifier import QStashSignatureVerifier

router = APIRouter(prefix="/internal/v1/integrations", tags=["internal-integrations"])


class Signal(BaseModel):
    organization_id: UUID
    command_id: UUID | None = None
    event_id: UUID | None = None
    job_id: UUID | None = None


@router.post("/process")
async def process(request: Request, upstash_signature: str | None = Header(default=None, alias="Upstash-Signature")):
    if not settings.integration_foundation_enabled:
        raise HTTPException(503, "Integration foundation is disabled; the request remains saved.")
    body = await request.body()
    try:
        if not upstash_signature:
            raise ValueError("Missing signature")
        QStashSignatureVerifier().verify(body=body.decode(), signature=upstash_signature, url=str(request.url))
        signal = Signal.model_validate(json.loads(body))
        if sum(bool(target) for target in (signal.command_id, signal.event_id, signal.job_id)) != 1:
            raise ValueError("Exactly one processing target is required")
    except Exception as error:
        raise HTTPException(401, "Invalid signed integration signal.") from error
    async with session_factory() as session:
        if signal.command_id:
            row = await session.get(ConversationCommand, signal.command_id)
            if not row or row.organization_id != signal.organization_id or row.family != "integration.execute":
                raise HTTPException(404, "Integration command not found.")
            if settings.smart_planner_enabled:
                from app.runtime.preparation_worker import start_preparation
                start_preparation(row.id, (row.payload or {}).get("activePreparationDecisionId"))
                return {"status": "accepted"}
            await prepare_integration_task(session, row.id)
        elif signal.job_id:
            job = await session.scalar(select(Job).where(Job.id == signal.job_id,
                Job.organization_id == signal.organization_id).with_for_update())
            if not job:
                raise HTTPException(404, "Integration job not found.")
            if settings.smart_planner_enabled:
                from app.runtime.preparation_worker import start_worker_preparation
                start_worker_preparation(job.id)
                return {"status": "accepted"}
            if job.status == "waiting_integration":
                revision = await session.scalar(select(JobRevision).where(JobRevision.job_id == job.id,
                    JobRevision.revision == job.current_revision))
                if revision and (revision.definition.get("autonomy") or {}).get("integrationFoundation"):
                    await reconcile_worker_job(session, ai_gateway_from_settings(session=session), job, revision)
        else:
            row = await session.get(IntegrationEventInbox, signal.event_id)
            if not row or row.organization_id != signal.organization_id:
                raise HTTPException(404, "Integration event not found.")
            await process_event(session, row.id)
        await session.commit()
    return {"status": "processed"}
