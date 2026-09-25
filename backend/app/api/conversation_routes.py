from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import require_permission
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
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.ai.provider import ai_gateway_from_settings
from app.infrastructure.database.session import database_session

v1_router = APIRouter(tags=["conversation"])
v2_router = APIRouter(tags=["conversation"])


class CreateThreadRequest(BaseModel):
    workerId: UUID | None = None
    title: str | None = Field(default=None, max_length=200)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    artifactReferences: list[dict[str, Any]] = Field(default_factory=list)


class UploadAttachmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    mediaType: str = Field(min_length=1, max_length=128)
    contentBase64: str = Field(min_length=1, max_length=15_000_000)
    question: str = Field(default="", max_length=4000)


def _http_error(exc: Exception) -> HTTPException:
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


async def _send(
    organization_id: UUID,
    thread_id: UUID,
    payload: SendMessageRequest,
    principal: HumanPrincipal,
    session: AsyncSession,
) -> dict[str, Any]:
    try:
        human, assistant, receipt = await ConversationService(session).send_message(
            organization_id=organization_id,
            principal=principal,
            thread_id=thread_id,
            content=payload.content,
            artifact_references=payload.artifactReferences,
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    return {
        "humanMessage": message_public(human),
        "response": message_public(assistant),
        "receipt": receipt_public(receipt),
    }


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
