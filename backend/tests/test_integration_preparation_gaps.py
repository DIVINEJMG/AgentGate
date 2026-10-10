from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import integration_tasks
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIGateway


@pytest.fixture(autouse=True)
def legacy_preparation(monkeypatch):
    monkeypatch.setattr(settings, "smart_planner_enabled", False)
from app.application.services.approval_conversation import approval_conversation_id
from app.application.services.conversation_commands import (
    CommandResolutionError,
    ConversationCommandCompiler,
)
from app.application.services.integration_foundation import (
    IntegrationFoundation,
    IntegrationRequirementError,
)
from app.application.services.integration_request_context import integration_request_context
from app.application.services.worker_site_targets import extract_site_assignments
from app.domain.ai.providers import AIProviderError
from app.domain.conversation.intent import WorkerCommandIntent
from app.domain.identity.principals import HumanPrincipal
from app.domain.integrations.foundation import IntegrationTaskDraft
from app.infrastructure.database.models import ConversationMessage, JobRevision, WorkerDirective


def test_task_context_preserves_references_and_attachment_findings_without_credentials():
    context = integration_request_context({
        "thread": {"messages": [{"content": "Review PR 17 in owner/repo", "token": "secret"}]},
        "relevantAttachments": [{"findings": {"requirement": "Check error handling"}}],
        "config": {"password": "secret"},
    })
    assert context["thread"]["messages"][0]["content"].endswith("owner/repo")
    assert "token" not in context["thread"]["messages"][0]
    assert context["relevantAttachments"][0]["findings"]["requirement"] == "Check error handling"
    assert "config" not in context


@pytest.mark.asyncio
async def test_native_worker_classification_does_not_invent_a_browser_site():
    gateway = SimpleNamespace(generate_structured=AsyncMock(return_value={"job_providers": {"0": ["github"]}}))
    result = await extract_site_assignments(gateway=cast(AIGateway, gateway),
        instruction="Create a worker to read pull requests from my connected GitHub account",
        job_names=["Read PRs"], organization_id=uuid4(), thread_id=uuid4())
    assert result.targets == [] and result.native_integrations == {"0": ["github"]}
    assert "NOT visiting websites" in gateway.generate_structured.call_args.kwargs["system"]


@pytest.mark.asyncio
async def test_compiler_gets_conversation_context_and_rejects_invented_standing_grant(monkeypatch):
    foundation = IntegrationFoundation(cast(AsyncSession, SimpleNamespace()))
    monkeypatch.setattr(foundation, "catalog", AsyncMock(return_value=[]))
    gateway = SimpleNamespace(generate_structured=AsyncMock(return_value={
        "objective": "Review PR", "needs": [{"provider": "github", "scopes": ["github.repository.issues.read"]}], "completion_criteria": ["Report"],
        "standing_request_excerpt": "Do this forever"}))
    with pytest.raises(IntegrationRequirementError):
        await foundation.interpret(cast(AIGateway, gateway), organization_id=uuid4(), user_id=uuid4(),
            instruction="Review that PR", conversation_context={"thread": {"messages": ["PR 17"]}})
    assert "PR 17" in gateway.generate_structured.call_args.kwargs["prompt"]


def preparation_session(role="owner", instruction="Review that PR", draft=None):
    org, thread, user, worker_id = uuid4(), uuid4(), uuid4(), uuid4()
    command = SimpleNamespace(id=uuid4(), organization_id=org, thread_id=thread, created_by=user,
        source_message_id=uuid4(), target_id=None, status="accepted", payload={"integrationTask": {
            "workerId": str(worker_id), "threadId": str(thread), "messageId": str(uuid4()),
            "instruction": instruction, "context": {"thread": {"messages": ["PR 17"]}}, "draft": draft}})
    worker = SimpleNamespace(id=worker_id, organization_id=org)
    records = []
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[command, SimpleNamespace(role=role), SimpleNamespace(role=role)]),
        get=AsyncMock(return_value=worker), add=records.append, flush=AsyncMock(), refresh=AsyncMock())
    return session, command, records


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [
    (AIProviderError("provider_unavailable", "offline", retryable=True), "waiting_ai"),
    (PermissionError("revoked"), "policy_denied"),
    (IntegrationRequirementError("Connect GitHub", missing=True), "waiting_integration"),
])
async def test_preparation_failures_report_distinct_saved_waits(monkeypatch, failure, status):
    session, command, records = preparation_session()
    interpret = AsyncMock(side_effect=failure)
    monkeypatch.setattr(integration_tasks.IntegrationFoundation, "interpret", interpret)
    monkeypatch.setattr(integration_tasks, "ai_gateway_from_settings", Mock(return_value=object()))
    monkeypatch.setattr(integration_tasks.TransactionalOutbox, "enqueue", AsyncMock())
    await integration_tasks.prepare_integration_task(session, command.id)
    assert command.status == status and command.target_id is None
    assert len([row for row in records if isinstance(row, ConversationMessage)]) == 1
    assert interpret.call_args.kwargs["conversation_context"]["thread"]["messages"] == ["PR 17"]
    if status in {"waiting_ai", "policy_denied"}:
        assert command.receipt["status"] == "unavailable"


@pytest.mark.asyncio
async def test_revoked_job_permissions_produce_a_worker_message(monkeypatch):
    session, command, records = preparation_session(role="viewer")
    monkeypatch.setattr(integration_tasks.TransactionalOutbox, "enqueue", AsyncMock())
    await integration_tasks.prepare_integration_task(session, command.id)
    assert command.status == "policy_denied"
    assert isinstance(records[0], ConversationMessage)
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_authority_is_rechecked_after_model_response(monkeypatch):
    draft = IntegrationTaskDraft.model_validate({"objective": "Read repository", "needs": [{"provider": "github",
        "scopes": ["github.repository.issues.read"]}], "completion_criteria": ["Report"]})
    session, command, records = preparation_session(draft=draft.model_dump())
    session.scalar.side_effect = [command, SimpleNamespace(role="owner"), SimpleNamespace(role="viewer")]
    resolve = AsyncMock()
    monkeypatch.setattr(integration_tasks.IntegrationFoundation, "resolve", resolve)
    monkeypatch.setattr(integration_tasks.TransactionalOutbox, "enqueue", AsyncMock())
    await integration_tasks.prepare_integration_task(session, command.id)
    assert command.status == "policy_denied"
    resolve.assert_not_awaited()
    assert all(not isinstance(row, JobRevision) for row in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("standing", [False, True])
async def test_explicit_standing_assignment_is_distinct_from_one_task(monkeypatch, standing):
    instruction = "Permanently review new PRs" if standing else "Review that PR"
    draft = IntegrationTaskDraft.model_validate({"objective": "Review PRs", "needs": [{"provider": "github", "scopes": ["github.repository.issues.read"]}], "completion_criteria": ["Report"],
        "standing_request_excerpt": instruction if standing else None})
    session, command, records = preparation_session(instruction=instruction, draft=draft.model_dump())
    monkeypatch.setattr(integration_tasks.IntegrationFoundation, "resolve", AsyncMock(return_value=[]))
    bind = AsyncMock()
    monkeypatch.setattr(integration_tasks.IntegrationFoundation, "bind", bind)
    monkeypatch.setattr(integration_tasks, "notify_integration_work", AsyncMock())
    monkeypatch.setattr(integration_tasks.TransactionalOutbox, "enqueue", AsyncMock())
    await integration_tasks.prepare_integration_task(session, command.id)
    revision = next(row for row in records if isinstance(row, JobRevision))
    assert revision.definition["autonomy"]["oneShot"] is not standing
    assert bind.call_args.kwargs["standing"] is standing
    assert bool([row for row in records if isinstance(row, WorkerDirective)]) is standing
    assert command.target_id is not None


@pytest.mark.asyncio
async def test_approval_is_bound_to_originating_conversation_and_tenant():
    org, thread = uuid4(), uuid4()
    action = SimpleNamespace(run_id=uuid4(), organization_id=org)
    session = SimpleNamespace(get=AsyncMock(side_effect=[
        SimpleNamespace(organization_id=org, work_item_id=uuid4()),
        SimpleNamespace(organization_id=org, payload={"integrationOrigin": {"threadId": str(thread)}})]))
    assert await approval_conversation_id(session, action) == str(thread)
    session.get = AsyncMock(return_value=SimpleNamespace(organization_id=uuid4()))
    assert await approval_conversation_id(session, action) is None


@pytest.mark.asyncio
async def test_legacy_connection_request_uses_accessible_exact_resource_catalog(monkeypatch):
    from app.bootstrap.settings import settings
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    org, user = uuid4(), uuid4()
    principal = HumanPrincipal(user_id=user, organization_id=org, membership_id=uuid4(),
        role="owner", permissions=frozenset({"integrations.read"}))
    rows = [{"provider": "github", "connectionId": str(uuid4()), "account": "Personal",
        "id": str(uuid4()), "externalId": "owner/AgentGate", "name": "AgentGate", "health": "healthy"}]
    catalog = AsyncMock(return_value=rows)
    monkeypatch.setattr(IntegrationFoundation, "catalog", catalog)
    compiler = ConversationCommandCompiler(cast(AsyncSession, SimpleNamespace()), cast(AIGateway, object()))
    kwargs = {"organization_id": org, "principal": principal, "thread": SimpleNamespace(),
        "source_message": SimpleNamespace(), "context": {}, "command": SimpleNamespace(id=uuid4()), "confirmed": False}
    receipt = await compiler._dispatch(intent=WorkerCommandIntent(family="integration.require",
        arguments={"provider": "github", "resource": "owner/AgentGate"}), **kwargs)
    assert receipt.status == "completed"
    catalog.assert_awaited_with(org, user)
    rows.append({**rows[0], "connectionId": str(uuid4()), "account": "Other"})
    with pytest.raises(CommandResolutionError):
        await compiler._dispatch(intent=WorkerCommandIntent(family="integration.require",
            arguments={"provider": "github"}), **kwargs)


@pytest.mark.asyncio
async def test_worker_creation_routing_precedence_is_in_interpreter_contract():
    from app.application.services.intent_interpreter import IntentInterpreter
    from app.domain.ai.providers import AIInvocationContext
    gateway = SimpleNamespace(generate_structured=AsyncMock(return_value={"family":"worker.create"}))
    result = await IntentInterpreter(cast(AIGateway, gateway)).interpret(message="Create a GitHub worker",
        context={}, invocation_context=AIInvocationContext(organization_id=uuid4()))
    assert result.family == "worker.create"
    assert "These examples are guidance" in gateway.generate_structured.call_args.kwargs["system"]
