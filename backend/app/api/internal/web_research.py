from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.application.services.web_research import ResearchOutcome, research_web
from app.infrastructure.ai.provider import ai_gateway_from_settings
from app.infrastructure.database.models import (
    ConversationCommand,
    ConversationMessage,
    ConversationThread,
)
from app.infrastructure.database.outbox import TransactionalOutbox
from app.infrastructure.database.session import session_factory
from app.infrastructure.qstash.verifier import QStashSignatureVerifier
from app.infrastructure.redis.coordination import RedisCoordinator

router = APIRouter(prefix="/internal/v1/web-research", tags=["internal-web-research"])


class ResearchPayload(BaseModel):
    organization_id: UUID
    command_id: UUID


@router.post("/execute")
async def execute_research(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, str]:
    raw = await request.body()
    try:
        if not upstash_signature:
            raise ValueError("Missing signature")
        QStashSignatureVerifier().verify(
            body=raw.decode("utf-8"), signature=upstash_signature, url=str(request.url)
        )
        payload = ResearchPayload.model_validate(json.loads(raw))
    except Exception as exc:
        raise HTTPException(401, "Invalid signed research request.") from exc

    coordinator = RedisCoordinator.from_settings()
    lease = None
    try:
        lease = await coordinator.acquire_lock(
            f"web-research:{payload.command_id}", ttl_seconds=180
        )
        if lease is None:
            raise HTTPException(503, "Research is already running; retry later.")
        async with session_factory() as session:
            command = await session.get(ConversationCommand, payload.command_id)
            if (
                command is None or command.organization_id != payload.organization_id
                or command.family != "web.research"
            ):
                raise HTTPException(404, "Research command not found.")
            if command.status != "accepted":
                return {"status": command.status}
            thread = await session.get(ConversationThread, command.thread_id)
            if thread is None or thread.organization_id != payload.organization_id:
                raise HTTPException(404, "Research conversation not found.")
            arguments = command.payload if isinstance(command.payload, dict) else {}
            gateway = ai_gateway_from_settings(session=session, workload="complex", purpose="web_research")
            try:
                async with asyncio.timeout(90):
                    outcome = await research_web(
                        query=str(arguments.get("query") or ""),
                        required_source=(
                            str(arguments["requiredSource"])
                            if arguments.get("requiredSource") else None
                        ),
                        required_site=(
                            str(arguments["requiredSite"])
                            if arguments.get("requiredSite") else None
                        ),
                        organization_id=payload.organization_id,
                        worker_id=thread.worker_id,
                        thread_id=thread.id,
                        gateway=gateway,
                    )
            except TimeoutError:
                outcome = ResearchOutcome(
                    "I could not complete the web research within the time limit. "
                    "Please try a narrower question.", (), ("research time limit",),
                )
            # QStash may redeliver. Canonical status and response are committed together.
            command = await session.get(
                ConversationCommand, payload.command_id, with_for_update=True,
            )
            if command is None or command.status != "accepted":
                return {"status": "already_completed"}
            command.status = "completed"
            command.receipt = {
                "status": "completed",
                "message": outcome.message[:12000],
                "attempts": list(outcome.attempts),
                "sources": [source.url for source in outcome.sources],
            }
            message = ConversationMessage(
                organization_id=payload.organization_id,
                thread_id=thread.id,
                role="worker" if thread.worker_id is not None else "system",
                content=outcome.message[:12000],
                artifact_references=[],
                command_references=[
                    {"id": str(command.id), "family": "web.research", "status": "completed"}
                ],
                result_references=[],
            )
            session.add(message)
            thread.updated_at = datetime.now(UTC)
            await session.flush()
            await TransactionalOutbox(session).enqueue(
                topic="conversation.response.created",
                aggregate_type="conversation_message",
                aggregate_id=str(message.id),
                payload={
                    "organization_id": str(payload.organization_id),
                    "worker_id": str(thread.worker_id) if thread.worker_id else None,
                    "resource_id": str(thread.id),
                    "thread_id": str(thread.id),
                    "message_id": str(message.id),
                    "role": message.role,
                },
            )
            await session.commit()
            return {"status": "completed"}
    finally:
        if lease is not None:
            await coordinator.release_lock(lease)
        await coordinator.close()
