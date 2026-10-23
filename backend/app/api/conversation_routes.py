from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.attachment_ingestion import (
    AttachmentIngestionService,
    decode_base64_content,
)
from app.application.services.conversation_commands import (
    CommandResolutionError,
    CrossTenantReferenceError,
)
from app.application.services.conversations import (
    ConversationService,
    message_public,
    receipt_public,
    thread_public,
)
from audoryn_shared.api.auth_dependencies import organization_principal
from audoryn_shared.api.product_common import require_permission
from audoryn_shared.application.services import entitlements
from audoryn_shared.domain.conversation.intent import CommandReceipt
from audoryn_shared.domain.identity.principals import HumanPrincipal
from audoryn_shared.infrastructure.ai.provider import ai_gateway_from_settings
from audoryn_shared.infrastructure.database.models import ConversationMessage
from audoryn_shared.infrastructure.database.session import database_session
from audoryn_shared.infrastructure.database.usage_records import record_usage
from audoryn_shared.observability.frames import app_frames
from audoryn_shared.observability.usage import Usage, metered

logger = logging.getLogger("uvicorn.error")

v1_router = APIRouter(tags=["conversation"])
v2_router = APIRouter(tags=["conversation"])


class CreateThreadRequest(BaseModel):
    workerId: UUID | None = None
    title: str | None = Field(default=None, max_length=200)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    artifactReferences: list[dict[str, Any]] = Field(default_factory=list)
    # Set by a screen that already knows the operation (the Create worker form), so the request
    # is not guessed from its wording.
    operation: Literal["worker.create"] | None = None
    # The question this message answers, when the screen knows it.
    resumeCommandId: UUID | None = None


class UploadAttachmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    mediaType: str = Field(min_length=1, max_length=128)
    contentBase64: str = Field(min_length=1, max_length=15_000_000)
    question: str = Field(default="", max_length=4000)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        # A service's own refusal (status and plain message) reaches the person unchanged.
        return exc
    if isinstance(exc, CrossTenantReferenceError):
        return HTTPException(403, str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, (ValueError, CommandResolutionError)):
        return HTTPException(400, str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(503, str(exc))
    # Where it failed in AgentGate's own code, and the first cause; the message itself may hold
    # conversation content, so it is not logged.
    cause = exc.__cause__ or exc.__context__
    logger.error("Conversation operation failed error_type=%s cause=%s at=%s", type(exc).__name__,
                 type(cause).__name__ if cause else None, app_frames(exc))
    return HTTPException(500, "Conversation operation failed.")


async def _create_thread(
    organization_id: UUID,
    payload: CreateThreadRequest,
    principal: HumanPrincipal,
    session: AsyncSession,
) -> dict[str, Any]:
    try:
        thread = await ConversationService(session).create_thread(
            organization_id=organization_id,
            principal=principal,
            worker_id=payload.workerId,
            title=payload.title,
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    return {"thread": thread_public(thread)}


@v1_router.post("/organizations/{organization_id}/conversations", status_code=201)
async def create_thread_v1(
    organization_id: UUID,
    payload: CreateThreadRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await _create_thread(organization_id, payload, principal, session)


@v2_router.post("/organizations/{organization_id}/conversations", status_code=201)
async def create_thread_v2(
    organization_id: UUID,
    payload: CreateThreadRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await _create_thread(organization_id, payload, principal, session)
    return {"data": result}


@v1_router.get("/organizations/{organization_id}/conversations")
async def list_threads_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
    worker_id: Annotated[UUID | None, Query(alias="workerId")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    try:
        rows = await ConversationService(session).list_threads(
            organization_id=organization_id,
            principal=principal,
            worker_id=worker_id,
            limit=limit,
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    return {"threads": [thread_public(row) for row in rows]}


@v2_router.get("/organizations/{organization_id}/conversations")
async def list_threads_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
    worker_id: Annotated[UUID | None, Query(alias="workerId")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    result = await list_threads_v1(organization_id, principal, session, worker_id, limit)
    return {"data": result}


@v1_router.get("/organizations/{organization_id}/conversations/{thread_id}")
async def get_thread_v1(
    organization_id: UUID,
    thread_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    try:
        thread, messages = await ConversationService(session).get_thread(
            organization_id=organization_id,
            principal=principal,
            thread_id=thread_id,
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    return {
        "thread": thread_public(thread),
        "messages": [message_public(message) for message in messages],
    }


@v2_router.get("/organizations/{organization_id}/conversations/{thread_id}")
async def get_thread_v2(
    organization_id: UUID,
    thread_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {"data": await get_thread_v1(organization_id, thread_id, principal, session)}


@v1_router.get("/organizations/{organization_id}/conversations/{thread_id}/activity")
@v2_router.get("/organizations/{organization_id}/conversations/{thread_id}/activity")
async def task_activity(
    organization_id: UUID,
    thread_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    from audoryn_shared.application.services.task_activity import conversation_activity
    from audoryn_shared.infrastructure.database.models import ConversationThread

    require_permission(principal, "workforce.read")
    # Same access boundary as opening the thread, without fetching its entire transcript.
    if principal.organization_id != organization_id:
        raise HTTPException(403, "Cross-organization conversation denied.")
    thread = await session.get(ConversationThread, thread_id)
    if not thread or thread.organization_id != organization_id:
        raise HTTPException(404, "Conversation not found.")
    return await conversation_activity(session, organization_id, thread_id, principal)


async def _upload_attachment(
    organization_id: UUID,
    thread_id: UUID,
    payload: UploadAttachmentRequest,
    principal: HumanPrincipal,
    session: AsyncSession,
) -> dict[str, Any]:
    require_permission(principal, "workforce.manage")
    try:
        thread, _messages = await ConversationService(session).get_thread(
            organization_id=organization_id,
            principal=principal,
            thread_id=thread_id,
        )
        content = decode_base64_content(payload.contentBase64)
        result = await AttachmentIngestionService(
            session,
            ai_gateway_from_settings(session=session),
        ).ingest_and_analyze(
            organization_id=organization_id,
            thread_id=thread.id,
            worker_id=thread.worker_id,
            name=payload.name,
            media_type=payload.mediaType,
            content=content,
            question=payload.question,
        )
    except Exception as exc:
        raise _http_error(exc) from exc

    meta = (
        dict(result.artifact.metadata_json)
        if isinstance(result.artifact.metadata_json, dict)
        else {}
    )
    return {
        "artifact": {
            "id": str(result.artifact.id),
            "name": str(meta.get("name") or payload.name),
            "mediaType": result.artifact.media_type,
            "sizeBytes": int(result.artifact.size_bytes or 0),
            "checksumSha256": result.artifact.checksum_sha256,
            "category": meta.get("category"),
        },
        "analysis": {
            "id": str(result.analysis.id),
            "status": result.analysis.status,
            "role": result.analysis.analyzer_role,
            "provider": result.analysis.provider,
            "model": result.analysis.model,
            "findings": result.analysis.findings,
        },
    }


@v1_router.post(
    "/organizations/{organization_id}/conversations/{thread_id}/attachments",
    status_code=201,
)
async def upload_attachment_v1(
    organization_id: UUID,
    thread_id: UUID,
    payload: UploadAttachmentRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await _upload_attachment(organization_id, thread_id, payload, principal, session)


@v2_router.post(
    "/organizations/{organization_id}/conversations/{thread_id}/attachments",
    status_code=201,
)
async def upload_attachment_v2(
    organization_id: UUID,
    thread_id: UUID,
    payload: UploadAttachmentRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await _upload_attachment(organization_id, thread_id, payload, principal, session)
    }


Reply = tuple[ConversationMessage, ConversationMessage, CommandReceipt]


async def _replied(session: AsyncSession, organization_id: UUID, thread_id: UUID,
                   reply: Callable[[], Awaitable[Reply]]) -> dict[str, Any]:
    """Run one reply to a message, metered and recorded, as the API's response."""
    await entitlements.require_allowance(session, organization_id, "ai.tokens.monthly")
    usage, outcome = Usage(), "error"
    try:
        with metered(usage):
            human, assistant, receipt = await reply()
        outcome = receipt.status
    except Exception as exc:
        raise _http_error(exc) from exc
    finally:
        await record_usage(usage, kind="conversation.message", organization_id=organization_id,
            thread_id=thread_id, phase=usage.labels.get("family"), outcome=outcome)
    return {
        "humanMessage": message_public(human),
        "response": message_public(assistant),
        "receipt": receipt_public(receipt),
    }


async def _send(
    organization_id: UUID,
    thread_id: UUID,
    payload: SendMessageRequest,
    principal: HumanPrincipal,
    session: AsyncSession,
) -> dict[str, Any]:
    return await _replied(session, organization_id, thread_id, lambda: ConversationService(session).send_message(
        organization_id=organization_id,
        principal=principal,
        thread_id=thread_id,
        content=payload.content,
        artifact_references=payload.artifactReferences,
        operation=payload.operation,
        resume_command_id=payload.resumeCommandId,
    ))


def _retry(organization_id: UUID, thread_id: UUID, message_id: UUID, principal: HumanPrincipal,
           session: AsyncSession) -> Awaitable[dict[str, Any]]:
    return _replied(session, organization_id, thread_id, lambda: ConversationService(session).retry_message(
        organization_id=organization_id, principal=principal, thread_id=thread_id, message_id=message_id))


@v1_router.post(
    "/organizations/{organization_id}/conversations/{thread_id}/messages/{message_id}/retry",
    status_code=201,
)
async def retry_message_v1(
    organization_id: UUID,
    thread_id: UUID,
    message_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    """Reply again to a saved message whose reply failed."""
    return await _retry(organization_id, thread_id, message_id, principal, session)


@v2_router.post(
    "/organizations/{organization_id}/conversations/{thread_id}/messages/{message_id}/retry",
    status_code=201,
)
async def retry_message_v2(
    organization_id: UUID,
    thread_id: UUID,
    message_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    """Reply again to a saved message whose reply failed."""
    return {"data": await _retry(organization_id, thread_id, message_id, principal, session)}


@v1_router.post(
    "/organizations/{organization_id}/conversations/{thread_id}/messages",
    status_code=201,
)
async def send_message_v1(
    organization_id: UUID,
    thread_id: UUID,
    payload: SendMessageRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await _send(organization_id, thread_id, payload, principal, session)


@v2_router.post(
    "/organizations/{organization_id}/conversations/{thread_id}/messages",
    status_code=201,
)
async def send_message_v2(
    organization_id: UUID,
    thread_id: UUID,
    payload: SendMessageRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {"data": await _send(organization_id, thread_id, payload, principal, session)}


async def _ask(
    organization_id: UUID,
    worker_id: UUID | None,
    payload: SendMessageRequest,
    principal: HumanPrincipal,
    session: AsyncSession,
) -> dict[str, Any]:
    service = ConversationService(session)
    try:
        thread = await service.get_or_create_scope_thread(
            organization_id=organization_id,
            principal=principal,
            worker_id=worker_id,
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    result = await _send(organization_id, thread.id, payload, principal, session)
    return {"thread": thread_public(thread), **result}


@v1_router.post("/organizations/{organization_id}/ask", status_code=201)
async def ask_aduoryn_v1(
    organization_id: UUID,
    payload: SendMessageRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await _ask(organization_id, None, payload, principal, session)


@v2_router.post("/organizations/{organization_id}/ask", status_code=201)
async def ask_aduoryn_v2(
    organization_id: UUID,
    payload: SendMessageRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {"data": await _ask(organization_id, None, payload, principal, session)}


@v1_router.post(
    "/organizations/{organization_id}/workforce/workers/{worker_id}/ask",
    status_code=201,
)
async def ask_worker_v1(
    organization_id: UUID,
    worker_id: UUID,
    payload: SendMessageRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await _ask(organization_id, worker_id, payload, principal, session)


@v2_router.post(
    "/organizations/{organization_id}/workforce/workers/{worker_id}/ask",
    status_code=201,
)
async def ask_worker_v2(
    organization_id: UUID,
    worker_id: UUID,
    payload: SendMessageRequest,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {"data": await _ask(organization_id, worker_id, payload, principal, session)}


@v1_router.post(
    "/organizations/{organization_id}/conversations/{thread_id}/commands/{command_id}/confirm"
)
async def confirm_command_v1(
    organization_id: UUID,
    thread_id: UUID,
    command_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    try:
        message, receipt = await ConversationService(session).confirm_command(
            organization_id=organization_id,
            principal=principal,
            thread_id=thread_id,
            command_id=command_id,
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    return {
        "response": message_public(message),
        "receipt": receipt_public(receipt),
    }


@v2_router.post(
    "/organizations/{organization_id}/conversations/{thread_id}/commands/{command_id}/confirm"
)
async def confirm_command_v2(
    organization_id: UUID,
    thread_id: UUID,
    command_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await confirm_command_v1(organization_id, thread_id, command_id, principal, session)
    }
