from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.conversation_commands import (
    ConversationCommandCompiler,
    CrossTenantReferenceError,
)
from app.application.services.conversation_context import ConversationContextAssembler
from app.application.services.intent_interpreter import IntentInterpreter
from app.domain.ai.providers import AIInvocationContext, AIProviderError
from app.domain.conversation.intent import CommandReceipt, FailureCategory
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.ai.provider import ai_gateway_from_settings
from app.infrastructure.database.models import (
    Artifact,
    ConversationCommand,
    ConversationMessage,
    ConversationThread,
    Worker,
)
from app.infrastructure.database.outbox import TransactionalOutbox


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_thread(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        worker_id: UUID | None = None,
        title: str | None = None,
    ) -> ConversationThread:
        self._require_org(principal, organization_id)
        if "workforce.read" not in principal.permissions:
            raise PermissionError("Missing required permission: workforce.read")
        worker = None
        if worker_id is not None:
            worker = await self._session.get(Worker, worker_id)
            if worker is None:
                raise LookupError("Worker not found.")
            if worker.organization_id != organization_id:
                raise CrossTenantReferenceError("Cross-tenant worker conversation denied.")
        thread = ConversationThread(
            organization_id=organization_id,
            worker_id=worker_id,
            title=(
                (title or "").strip()
                or (f"Message {worker.name}" if worker is not None else "Ask Aduoryn")
            )[:200],
            status="active",
            created_by=principal.user_id,
        )
        self._session.add(thread)
        await self._session.flush()
        await TransactionalOutbox(self._session).enqueue(
            topic="conversation.thread.created",
            aggregate_type="conversation_thread",
            aggregate_id=str(thread.id),
            payload={
                "organization_id": str(organization_id),
                "worker_id": str(worker_id) if worker_id else None,
                "resource_id": str(thread.id),
                "thread_id": str(thread.id),
            },
        )
        await self._session.commit()
        await self._session.refresh(thread)
        return thread

    async def get_or_create_scope_thread(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        worker_id: UUID | None,
    ) -> ConversationThread:
        self._require_org(principal, organization_id)
        statement = (
            select(ConversationThread)
            .where(
                ConversationThread.organization_id == organization_id,
                ConversationThread.created_by == principal.user_id,
                ConversationThread.status == "active",
            )
            .order_by(desc(ConversationThread.updated_at))
            .limit(1)
        )
        if worker_id is None:
            statement = statement.where(ConversationThread.worker_id.is_(None))
        else:
            statement = statement.where(ConversationThread.worker_id == worker_id)
        thread = await self._session.scalar(statement)
        if thread is not None:
            return thread
        return await self.create_thread(
            organization_id=organization_id,
            principal=principal,
            worker_id=worker_id,
        )

    async def list_threads(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        worker_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ConversationThread]:
        self._require_org(principal, organization_id)
        if "workforce.read" not in principal.permissions:
            raise PermissionError("Missing required permission: workforce.read")
        statement = (
            select(ConversationThread)
            .where(ConversationThread.organization_id == organization_id)
            .order_by(desc(ConversationThread.updated_at))
            .limit(min(200, max(1, limit)))
        )
        if worker_id is not None:
            statement = statement.where(ConversationThread.worker_id == worker_id)
        return list((await self._session.scalars(statement)).all())

    async def get_thread(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        thread_id: UUID,
    ) -> tuple[ConversationThread, list[ConversationMessage]]:
        self._require_org(principal, organization_id)
        if "workforce.read" not in principal.permissions:
            raise PermissionError("Missing required permission: workforce.read")
        thread = await self._session.get(ConversationThread, thread_id)
        if thread is None:
            raise LookupError("Conversation not found.")
        if thread.organization_id != organization_id:
            raise CrossTenantReferenceError("Cross-tenant conversation denied.")
        messages = list(
            (
                await self._session.scalars(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.organization_id == organization_id,
                        ConversationMessage.thread_id == thread_id,
                    )
                    .order_by(ConversationMessage.created_at)
                    .limit(500)
                )
            ).all()
        )
        return thread, messages

    async def send_message(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        thread_id: UUID,
        content: str,
        artifact_references: list[dict[str, Any]] | None = None,
    ) -> tuple[ConversationMessage, ConversationMessage, CommandReceipt]:
        self._require_org(principal, organization_id)
        thread, _ = await self.get_thread(
            organization_id=organization_id,
            principal=principal,
            thread_id=thread_id,
        )
        clean = content.strip()
        if not clean:
            raise ValueError("Message content is required.")
        artifacts = await self._validated_artifact_refs(organization_id, artifact_references or [])
        human = ConversationMessage(
            organization_id=organization_id,
            thread_id=thread.id,
            role="human",
            content=clean[:12000],
            artifact_references=artifacts,
            command_references=[],
            result_references=[],
        )
        self._session.add(human)
        if thread.title in {"Ask Aduoryn", "New conversation"} or thread.title.startswith(
            "Message "
        ):
            thread.title = clean[:80]
        await self._session.flush()
        await self._message_event(thread, human)

        gateway = ai_gateway_from_settings(session=self._session)
        context = await ConversationContextAssembler(self._session).assemble(
            organization_id=organization_id,
            thread=thread,
        )
        invocation_context = AIInvocationContext(
            organization_id=organization_id,
            worker_id=thread.worker_id,
            thread_id=thread.id,
            correlation_id=f"conversation:{human.id}",
        )

        try:
            intent = await IntentInterpreter(gateway).interpret(
                message=clean,
                context=context,
                invocation_context=invocation_context,
            )
            outcome = await ConversationCommandCompiler(self._session, gateway).compile_and_execute(
                organization_id=organization_id,
                principal=principal,
                thread=thread,
                source_message=human,
                intent=intent,
                authoritative_context=context,
            )
            receipt = outcome.receipt
            command_refs = [
                {
                    "id": str(outcome.command.id),
                    "family": outcome.command.family,
                    "status": outcome.command.status,
                }
            ]
        except AIProviderError as exc:
            receipt = CommandReceipt(
                status="unavailable",
                message=self._provider_message(exc),
                failure_category=self._provider_failure_category(exc),
            )
            command_refs = []

        assistant = ConversationMessage(
            organization_id=organization_id,
            thread_id=thread.id,
            role="worker" if thread.worker_id is not None else "system",
            content=receipt.message[:12000],
            artifact_references=[],
            command_references=command_refs,
            result_references=[
                reference.model_dump(mode="json") for reference in receipt.result_references
            ],
        )
        self._session.add(assistant)
        await self._session.flush()
        await self._message_event(thread, assistant)
        await self._session.commit()
        await self._session.refresh(human)
        await self._session.refresh(assistant)
        return human, assistant, receipt

    async def confirm_command(
        self,
        *,
        organization_id: UUID,
        principal: HumanPrincipal,
        thread_id: UUID,
        command_id: UUID,
    ) -> tuple[ConversationMessage, CommandReceipt]:
        self._require_org(principal, organization_id)
        thread, _ = await self.get_thread(
            organization_id=organization_id,
            principal=principal,
            thread_id=thread_id,
        )
        command = await self._session.get(ConversationCommand, command_id)
        if command is None:
            raise LookupError("Conversation command not found.")
        if command.organization_id != organization_id or command.thread_id != thread.id:
            raise CrossTenantReferenceError("Cross-tenant command denied.")
        source = await self._session.get(ConversationMessage, command.source_message_id)
        if source is None:
            raise LookupError("Command source message is unavailable.")
        context = await ConversationContextAssembler(self._session).assemble(
            organization_id=organization_id,
            thread=thread,
        )
        gateway = ai_gateway_from_settings(session=self._session)
        outcome = await ConversationCommandCompiler(self._session, gateway).confirm(
            organization_id=organization_id,
            principal=principal,
            command=command,
            thread=thread,
            source_message=source,
            authoritative_context=context,
        )
        response = ConversationMessage(
            organization_id=organization_id,
            thread_id=thread.id,
            role="worker" if thread.worker_id is not None else "system",
            content=outcome.receipt.message,
            artifact_references=[],
            command_references=[
                {
                    "id": str(command.id),
                    "family": command.family,
                    "status": command.status,
                }
            ],
            result_references=[
                reference.model_dump(mode="json") for reference in outcome.receipt.result_references
            ],
        )
        self._session.add(response)
        await self._session.flush()
        await self._message_event(thread, response)
        await self._session.commit()
        await self._session.refresh(response)
        return response, outcome.receipt

    async def _validated_artifact_refs(
        self,
        organization_id: UUID,
        references: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        validated: list[dict[str, Any]] = []
        for reference in references[:12]:
            raw_id = reference.get("id")
            try:
                artifact_id = UUID(str(raw_id))
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid artifact reference.") from exc
            artifact = await self._session.get(Artifact, artifact_id)
            if artifact is None:
                raise LookupError("Artifact not found.")
            if artifact.organization_id != organization_id:
                raise CrossTenantReferenceError("Cross-tenant artifact denied.")
            validated.append(
                {
                    "id": str(artifact.id),
                    "mediaType": artifact.media_type,
                }
            )
        return validated

    async def _message_event(
        self,
        thread: ConversationThread,
        message: ConversationMessage,
    ) -> None:
        await TransactionalOutbox(self._session).enqueue(
            topic=(
                "conversation.response.created"
                if message.role in {"worker", "system"}
                else "conversation.message.created"
            ),
            aggregate_type="conversation_message",
            aggregate_id=str(message.id),
            payload={
                "organization_id": str(message.organization_id),
                "worker_id": str(thread.worker_id) if thread.worker_id else None,
                "resource_id": str(thread.id),
                "thread_id": str(thread.id),
                "message_id": str(message.id),
                "role": message.role,
            },
        )

    def _provider_failure_category(self, error: AIProviderError) -> FailureCategory:
        if error.category == "authentication_failed":
            return "authentication_expiry"
        if error.category == "configuration_missing":
            return "internal_platform_failure"
        return "provider_model_outage"

    def _provider_message(self, error: AIProviderError) -> str:
        if error.category in {
            "configuration_missing",
            "authentication_failed",
            "model_not_found",
        }:
            return (
                "Aduoryn's AI command interpreter is not configured right now. "
                "No worker or external state was changed."
            )
        if error.retryable:
            return (
                "Aduoryn's AI command interpreter is temporarily unavailable. "
                "No worker or external state was changed."
            )
        return "I could not safely interpret that request. No worker or external state was changed."

    def _require_org(
        self,
        principal: HumanPrincipal,
        organization_id: UUID,
    ) -> None:
        if principal.organization_id != organization_id:
            raise CrossTenantReferenceError("Cross-organization conversation denied.")


def thread_public(thread: ConversationThread) -> dict[str, Any]:
    return {
        "id": str(thread.id),
        "organizationId": str(thread.organization_id),
        "workerId": str(thread.worker_id) if thread.worker_id else None,
        "title": thread.title,
        "status": thread.status,
        "createdBy": str(thread.created_by),
        "createdAt": thread.created_at.isoformat(),
        "updatedAt": thread.updated_at.isoformat(),
    }


def message_public(message: ConversationMessage) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "organizationId": str(message.organization_id),
        "threadId": str(message.thread_id),
        "role": message.role,
        "content": message.content,
        "artifactReferences": list(message.artifact_references or []),
        "commandReferences": list(message.command_references or []),
        "resultReferences": list(message.result_references or []),
        "createdAt": message.created_at.isoformat(),
    }


def receipt_public(receipt: CommandReceipt) -> dict[str, Any]:
    return receipt.model_dump(mode="json")
