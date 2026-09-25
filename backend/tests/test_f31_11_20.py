from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.conversation_routes import v1_router, v2_router
from app.application.services.conversation_commands import ConversationCommandCompiler
from app.application.services.conversation_context import ConversationContextAssembler
from app.application.services.conversations import ConversationService
from app.application.services.intent_interpreter import IntentInterpreter
from app.domain.ai.providers import (
    AIInvocationContext,
    AIMediaInput,
    AIModelRole,
    AIProviderError,
    AIResponse,
)
from app.domain.conversation.intent import EntityReference, WorkerCommandIntent
from app.infrastructure.database.models import (
    ConversationCommand,
    ConversationMessage,
    ConversationThread,
    Worker,
    WorkerDirective,
)
from app.realtime.contracts import REALTIME_EVENT_TYPES


@dataclass
class IntentGateway:
    payload: dict[str, object]
    role_seen: AIModelRole | None = None
    prompt_seen: str = ""

    async def generate_structured(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, object],
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> dict[str, object]:
        del system, schema, context, max_output_tokens, temperature
        self.role_seen = role
        self.prompt_seen = prompt
        assert schema_name == "worker_command_intent_v1"
        return self.payload

    async def generate_text(self, **_: Any) -> AIResponse:
        raise AssertionError("intent test must use structured generation")

    async def analyze_media(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        media: AIMediaInput,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
    ) -> AIResponse:
        del role, system, prompt, media, context, max_output_tokens
        raise AssertionError("intent test must not use media analysis")


@pytest.mark.asyncio
async def test_intent_interpreter_uses_intent_role_and_authoritative_context() -> None:
    gateway = IntentGateway(
        {
            "family": "worker.status",
            "worker": {"name": "QQ"},
            "arguments": {},
            "response_hint": "",
            "confidence": 0.99,
        }
    )
    interpreter = IntentInterpreter(gateway)
    intent = await interpreter.interpret(
        message="QQ, what are you doing?",
        context={"worker": {"id": "w1", "name": "QQ", "status": "active"}},
        invocation_context=AIInvocationContext(correlation_id="test"),
    )

    assert intent.family == "worker.status"
    assert intent.worker is not None and intent.worker.name == "QQ"
    assert gateway.role_seen == "intent"
    assert "AUTHORITATIVE_CONTEXT" in gateway.prompt_seen
    assert '"status":"active"' in gateway.prompt_seen


@pytest.mark.asyncio
async def test_intent_interpreter_rejects_unknown_command_family() -> None:
    gateway = IntentGateway(
        {
            "family": "browser.click_anything",
            "arguments": {},
            "response_hint": "",
            "confidence": 1.0,
        }
    )
    with pytest.raises(ValidationError):
        await IntentInterpreter(gateway).interpret(
            message="do something unsafe",
            context={},
            invocation_context=AIInvocationContext(),
        )


def test_worker_command_intent_contains_all_f31_13_families() -> None:
    schema = WorkerCommandIntent.model_json_schema()
    family_schema = schema["properties"]["family"]
    expected = {
        "worker.create",
        "worker.update",
        "worker.status",
        "worker.pause",
        "worker.resume",
        "worker.delete",
        "job.create",
        "job.update",
        "job.stop",
        "job.retry",
        "job.status",
        "schedule.create",
        "schedule.update",
        "schedule.pause",
        "schedule.resume",
        "policy.add",
        "policy.update",
        "policy.remove",
        "instruction.add",
        "instruction.remove",
        "result.query",
        "failure.explain",
        "work.execute_now",
        "integration.require",
        "attachment.analyze",
        "conversation.answer",
    }
    assert set(family_schema["enum"]) == expected


def test_conversation_persistence_models_are_tenant_owned_and_reference_canonical_data() -> None:
    thread_columns = set(ConversationThread.__table__.columns.keys())
    message_columns = set(ConversationMessage.__table__.columns.keys())
    command_columns = set(ConversationCommand.__table__.columns.keys())
    directive_columns = set(WorkerDirective.__table__.columns.keys())

    assert {
        "organization_id",
        "worker_id",
        "title",
        "status",
        "created_by",
        "created_at",
        "updated_at",
    }.issubset(thread_columns)
    assert {
        "organization_id",
        "thread_id",
        "role",
        "content",
        "artifact_references",
        "command_references",
        "result_references",
        "created_at",
    }.issubset(message_columns)
    assert {
        "organization_id",
        "thread_id",
        "source_message_id",
        "family",
        "status",
        "target_type",
        "target_id",
        "payload",
        "receipt",
        "created_by",
        "requires_confirmation",
    }.issubset(command_columns)
    assert {"organization_id", "worker_id", "text", "status"}.issubset(directive_columns)


def test_conversation_context_is_bounded_and_does_not_import_secret_credentials() -> None:
    source = inspect.getsource(ConversationContextAssembler)
    assert "MAX_MESSAGES" in inspect.getsource(
        __import__(
            "app.application.services.conversation_context",
            fromlist=["MAX_MESSAGES"],
        )
    )
    assert "IntegrationCredential" not in source
    assert "storage_key" not in source
    assert "ciphertext" not in source
    assert "secret_hash" not in source
    assert "connectedIntegrations" in source
    assert "currentWork" in source
    assert "recentRuns" in source
    assert "recentResults" in source
    assert "relevantAttachments" in source


def test_org_and_worker_conversation_surfaces_are_registered_in_v1_and_v2() -> None:
    v1_paths = {str(getattr(route, "path", "")) for route in v1_router.routes}
    v2_paths = {str(getattr(route, "path", "")) for route in v2_router.routes}
    required = {
        "/organizations/{organization_id}/conversations",
        "/organizations/{organization_id}/conversations/{thread_id}",
        "/organizations/{organization_id}/conversations/{thread_id}/messages",
        "/organizations/{organization_id}/ask",
        "/organizations/{organization_id}/workforce/workers/{worker_id}/ask",
        "/organizations/{organization_id}/conversations/{thread_id}/commands/{command_id}/confirm",
    }
    assert required.issubset(v1_paths)
    assert required.issubset(v2_paths)


def test_conversation_realtime_events_cover_responses_receipts_and_wait_states() -> None:
    assert {
        "conversation.thread.created",
        "conversation.message.created",
        "conversation.response.created",
        "conversation.command.accepted",
        "conversation.command.completed",
        "conversation.clarification_required",
        "conversation.approval_required",
        "conversation.integration_required",
        "worker.status.changed",
        "run.failed",
        "run.completed",
        "result.created",
        "approval.created",
    }.issubset(REALTIME_EVENT_TYPES)


def test_submit_approval_directive_compiles_to_existing_browser_submit_scope() -> None:
    session = cast(AsyncSession, object())
    gateway = cast(Any, object())
    compiler = ConversationCommandCompiler(session, gateway)
    worker = Worker(
        organization_id=uuid4(),
        agent_identity_id=uuid4(),
        name="QQ",
        department="Research",
        status="active",
        profile={},
    )
    intent = WorkerCommandIntent(
        family="policy.add",
        worker=EntityReference(name="QQ"),
        arguments={
            "approvalBoundary": "submit",
            "directive": "Never submit without asking me.",
        },
    )

    payload = compiler._policy_payload(intent, worker)

    assert payload["effect"] == "require_approval"
    assert payload["selectors"]["scopes"] == ["browser.form.submit"]
    assert payload["selectors"]["agentIds"] == [str(worker.agent_identity_id)]


def test_provider_outage_message_never_claims_business_task_failed() -> None:
    service = ConversationService(cast(AsyncSession, object()))
    retryable = AIProviderError(
        "provider_unavailable",
        "provider down",
        retryable=True,
    )
    configuration = AIProviderError(
        "configuration_missing",
        "no key",
        retryable=False,
    )

    assert "No worker or external state was changed" in service._provider_message(retryable)
    assert "No worker or external state was changed" in service._provider_message(configuration)
    assert "job failed" not in service._provider_message(retryable).lower()


def test_conversation_compiler_has_no_direct_side_effect_provider_dependency() -> None:
    source = inspect.getsource(ConversationCommandCompiler)
    module_source = inspect.getsource(
        __import__(
            "app.application.services.conversation_commands",
            fromlist=["ConversationCommandCompiler"],
        )
    )
    assert "NvidiaNimProvider" not in module_source
    assert "UniversalProviderExecutor" not in module_source
    assert "ActionGateway" not in module_source
    assert "create_job(" in source
    assert "save_trigger_config(" in source
    assert "_create_policy(" in source
    assert "cancel_item(" in source
    assert "set_worker_status(" in source


def test_destructive_and_cross_tenant_safety_paths_exist() -> None:
    source = inspect.getsource(ConversationCommandCompiler)
    assert 'status="waiting_confirmation"' in source
    assert "CrossTenantReferenceError" in source
    assert "multiple workers matching" in source
    assert "multiple possible jobs" in source


def test_database_schema_migration_switch_defaults_off() -> None:
    from app.bootstrap.settings import Settings

    config = Settings()
    assert config.database_migrate_on_startup is False


def test_lifespan_runs_schema_migration_only_behind_feature_flag() -> None:
    from app.bootstrap import lifecycle

    source = inspect.getsource(lifecycle)
    assert "settings.database_migrate_on_startup" in source
    assert "await upgrade_database_schema()" in source
