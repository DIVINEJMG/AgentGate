from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.services.integration_foundation import (
    IntegrationFoundation,
    IntegrationRequirementError,
)
from app.application.services.integration_worker_readiness import reconcile_worker_job
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIProviderError
from app.domain.integrations.foundation import IntegrationNeed, IntegrationTaskDraft
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.registry import ProviderRegistry
from app.infrastructure.database.models import JobRevision, WorkItem


def fake_session(**kwargs) -> Any:
    return SimpleNamespace(**kwargs)


@pytest.fixture
def github(monkeypatch):
    monkeypatch.setattr(settings, "smart_planner_enabled", False)
    monkeypatch.setattr(settings, "coding_execution_enabled", True)
    monkeypatch.setattr(settings, "e2b_api_key", "test-only")
    monkeypatch.setattr(settings, "coding_template_id", "test-only")
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    provider = ExpandedGitHubProvider()
    monkeypatch.setattr("app.application.services.integration_foundation.execution_provider_registry",
        lambda: ProviderRegistry((provider,)))
    return provider


def draft(scopes=None, runtime=True):
    return IntegrationTaskDraft(objective="Inspect and edit the authorized repository",
        completion_criteria=["Report verified tests"], runtime_requirements=["e2b"] if runtime else [],
        needs=[IntegrationNeed(provider="github", resource_hint="owner/AgentGate",
            scopes=scopes or ["github.repository.workspace.open"],
            constraints={"allowedPaths": ["backend/scripts/check.py"]})])


def resource(name="owner/AgentGate", scopes=None):
    return {"id": str(uuid4()), "connectionId": str(uuid4()), "provider": "github",
        "externalId": "123", "name": name, "account": "owner", "health": "healthy",
        "capabilities": scopes or ["github.repository.workspace.open", "github.repository.branch.read", "github.repository.metadata.read"], "aliases": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope, expected", [
    ("github.repository.workspace.open", ["e2b"]),
    ("github.repository.contents.read", []),
])
async def test_runtime_follows_ai_selected_tools_without_human_naming_e2b(github, monkeypatch, scope, expected):
    foundation = IntegrationFoundation(fake_session())
    monkeypatch.setattr(foundation, "catalog", AsyncMock(return_value=[resource()]))
    response = draft([scope], runtime=False).model_dump(mode="json")
    gateway: Any = SimpleNamespace(generate_structured=AsyncMock(return_value=response))
    compiled = await foundation.interpret(gateway, organization_id=uuid4(), user_id=uuid4(),
        instruction="Edit the requested file and run tests in owner/AgentGate.")
    assert compiled.runtime_requirements == expected
    assert compiled.needs[0].scopes == github.task_prerequisites([scope])
    assert "human never names E2B" in gateway.generate_structured.call_args.kwargs["system"]


@pytest.mark.asyncio
async def test_coding_runtime_is_not_a_provider_and_requires_workspace_tools(github, monkeypatch):
    foundation = IntegrationFoundation(fake_session())
    monkeypatch.setattr(foundation, "catalog", AsyncMock(return_value=[resource()]))
    assert foundation.runtime_catalog()["e2b"]["provider"] == "github"
    with pytest.raises(AIProviderError, match="omitted its workspace capabilities"):
        await foundation.resolve(organization_id=uuid4(), user_id=uuid4(),
            draft=draft(["github.repository.contents.read"]))
    bindings = await foundation.resolve(organization_id=uuid4(), user_id=uuid4(), draft=draft())
    assert bindings[0]["scopes"] == github.task_prerequisites(["github.repository.workspace.open"])
    assert bindings[0]["constraints"]["allowedPaths"] == ["backend/scripts/check.py"]


@pytest.mark.asyncio
async def test_unconfigured_runtime_reports_setup_not_missing_account(github, monkeypatch):
    foundation = IntegrationFoundation(fake_session())
    monkeypatch.setattr(foundation, "catalog", AsyncMock(return_value=[]))
    monkeypatch.setattr(github, "runtime_catalog", lambda: {"e2b": {"provider": "github", "scopes": [], "reason": "Missing template"}})
    with pytest.raises(IntegrationRequirementError, match="Missing template"):
        await foundation.resolve(organization_id=uuid4(), user_id=uuid4(), draft=draft())


def test_refresh_changes_runtime_availability_without_expanding_business_authority(github):
    row = SimpleNamespace(capabilities=["github.repository.contents.read"], resource_type="repository", health="healthy")
    state = SimpleNamespace(credential_metadata={"scopes": ["contents:read"]})
    caps = github.resource_capabilities(resource=row, connection=SimpleNamespace(config={}), state=state)
    assert "github.repository.workspace.open" in caps
    assert "github.repository.commit.create" not in caps
    assert "github.repository.workspace.edit" in caps  # sandbox editing does not publish to GitHub
    grant = ["github.repository.contents.read"]
    assert "github.repository.workspace.open" not in grant


@pytest.mark.asyncio
async def test_current_resource_catalog_refreshes_stale_runtime_caps(github):
    row = SimpleNamespace(provider="github", connection_id=uuid4(), resource_type="repository",
        health="healthy", capabilities=["github.repository.contents.read"])
    state = SimpleNamespace(authorization_state="connected", credential_metadata={"scopes": ["contents:write"]})
    session = fake_session(get=AsyncMock(return_value=SimpleNamespace(config={})), scalar=AsyncMock(return_value=state))
    caps = await IntegrationFoundation(session).resource_capabilities(row)
    assert "github.repository.workspace.edit" in caps
    assert caps == row.capabilities
    state.authorization_state = "reconnect_required"
    assert await IntegrationFoundation(session).resource_capabilities(row) == []


@pytest.mark.asyncio
async def test_explicit_chat_repository_does_not_inherit_standing_repository(github, monkeypatch):
    foundation = IntegrationFoundation(fake_session())
    a, b = resource(), resource("owner/Other")
    monkeypatch.setattr(foundation, "catalog", AsyncMock(return_value=[a, b]))
    wrong = draft().model_copy(update={"needs": [draft().needs[0].model_copy(update={"resource_hint": "owner/Other"})]})
    with pytest.raises(IntegrationRequirementError, match="explicitly requested"):
        await foundation.resolve(organization_id=uuid4(), user_id=uuid4(), draft=wrong,
            instruction="In owner/AgentGate edit backend/scripts/check.py")
    bindings = await foundation.resolve(organization_id=uuid4(), user_id=uuid4(), draft=draft(),
        instruction="In owner/AgentGate edit backend/scripts/check.py")
    assert bindings[0]["id"] == a["id"]


@pytest.mark.asyncio
async def test_matching_resource_missing_scope_has_precise_reason(github, monkeypatch):
    foundation = IntegrationFoundation(fake_session())
    monkeypatch.setattr(foundation, "catalog", AsyncMock(return_value=[resource(scopes=["github.repository.contents.read"])]))
    with pytest.raises(IntegrationRequirementError, match="workspace.open"):
        await foundation.resolve(organization_id=uuid4(), user_id=uuid4(), draft=draft())


@pytest.mark.asyncio
@pytest.mark.parametrize("different_resource", [False, True])
async def test_existing_incomplete_grant_is_rebuilt_without_mutating_old_revision(github, monkeypatch, different_resource):
    row = resource()
    old_scope = "github.repository.contents.read"
    old_grant = SimpleNamespace(resource_id=uuid4() if different_resource else row["id"],
        scopes=[old_scope], constraints={}, destinations={}, authority_version=1,
        initiating_user_id=uuid4(), standing=True, work_item_id=None)
    org, user, worker_id = uuid4(), uuid4(), uuid4()
    revision = SimpleNamespace(id=uuid4(), revision=1, created_by=user, definition={"instructions": "Edit in E2B",
        "requiredCapabilities": [old_scope], "autonomy": {"missingIntegrations": ["e2b"],
            "capabilityNeeds": [{"provider": "e2b"}], "startWhenReady": False}})
    job = SimpleNamespace(id=uuid4(), organization_id=org, worker_id=worker_id, current_revision=1, status="waiting_integration")
    added = []
    session = fake_session(scalar=AsyncMock(side_effect=[SimpleNamespace(role="owner"), SimpleNamespace(role="owner"), SimpleNamespace(authority_version=1)]),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [old_grant])),
        get=AsyncMock(return_value=SimpleNamespace(id=worker_id)), add=added.append, flush=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(IntegrationFoundation, "interpret", AsyncMock(return_value=draft()))
    monkeypatch.setattr(IntegrationFoundation, "resolve", AsyncMock(return_value=[{**row, "scopes": draft().needs[0].scopes,
        "constraints": draft().needs[0].constraints, "destinations": {}}]))
    bind = AsyncMock()
    monkeypatch.setattr(IntegrationFoundation, "bind", bind)
    result = await reconcile_worker_job(session, object(), job, revision)
    if different_resource:
        assert not result and job.status == "waiting_integration"
        waiting = next(r for r in added if isinstance(r, JobRevision))
        assert "different resources" in waiting.definition["autonomy"]["integrationWaitReason"]
        assert "integrationWaitReason" not in revision.definition["autonomy"]
        bind.assert_not_awaited()
    else:
        assert result and job.current_revision == 2 and job.status == "active"
        new = next(r for r in added if isinstance(r, JobRevision))
        assert new.definition["requiredCapabilities"] == ["github.repository.workspace.open"]
        assert new.definition["autonomy"]["missingIntegrations"] == []
        assert revision.definition["requiredCapabilities"] == [old_scope]
        assert old_grant.scopes == [old_scope]
        bind.assert_awaited_once()


@pytest.mark.asyncio
async def test_planner_excludes_revoked_connection_with_execution_reason(github, monkeypatch):
    from app.runtime import managed
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    monkeypatch.setattr(managed, "_catalog", AsyncMock(return_value={"resources": []}))
    org, rid, wid = uuid4(), uuid4(), uuid4()
    grant = SimpleNamespace(resource_id=rid, worker_id=wid, scopes=["github.repository.workspace.open"])
    row = SimpleNamespace(id=rid, organization_id=org, provider="github", display_name="owner/AgentGate")
    worker = SimpleNamespace(id=wid, agent_identity_id=uuid4())
    session = fake_session(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [grant])),
        get=AsyncMock(side_effect=[SimpleNamespace(worker_id=wid), row, worker]))
    executor = object.__new__(managed.ManagedRuntimeExecutor)
    executor._session, executor._registry = session, ProviderRegistry((github,))
    monkeypatch.setattr(IntegrationFoundation, "authorize", AsyncMock(side_effect=PermissionError("Connection authority changed")))
    catalog = await executor._execution_catalog(cast(WorkItem, SimpleNamespace(id=uuid4(), job_id=uuid4(), organization_id=org, job_revision_id=uuid4())))
    assert catalog["resources"] == []
    assert catalog["capabilityExclusions"][0]["reason"] == "Connection authority changed"


@pytest.mark.asyncio
async def test_resource_refresh_queues_saved_chat_and_current_worker_preparation(monkeypatch):
    from app.infrastructure.database.outbox import TransactionalOutbox
    org, user, job_id, command_id = uuid4(), uuid4(), uuid4(), uuid4()
    session = fake_session(scalars=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: [SimpleNamespace(id=command_id)]),
        SimpleNamespace(all=lambda: [SimpleNamespace(job_id=job_id,
            definition={"autonomy": {"integrationFoundation": True}})])]))
    enqueue = AsyncMock()
    monkeypatch.setattr(TransactionalOutbox, "enqueue", enqueue)
    await IntegrationFoundation(session).queue_waiting_preparation(org, user)
    assert [call.kwargs["topic"] for call in enqueue.await_args_list] == [
        "integration.task.prepare", "integration.worker.prepare"]
    assert enqueue.await_args_list[1].kwargs["payload"] == {
        "organization_id": str(org), "job_id": str(job_id)}
