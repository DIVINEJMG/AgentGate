from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api import governance_routes as governance
from app.application.services.integration_foundation import IntegrationFoundation
from app.application.services.live_results import live_result_reference
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIGateway
from app.domain.identity.principals import HumanPrincipal
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.registry import ProviderRegistry
from app.infrastructure.database.models import (
    AgentIdentity,
    Job,
    JobRevision,
    Result,
    Run,
    RunStep,
    Worker,
    WorkItem,
)
from app.runtime.managed import _system_principal


@pytest.fixture
def resource_setup(monkeypatch):
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    provider = ExpandedGitHubProvider()
    monkeypatch.setattr("app.execution.bootstrap.execution_provider_registry", lambda: ProviderRegistry((provider,)))
    org, rid, aid = uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(id=rid, organization_id=org, provider="github", connection_id=uuid4(),
        display_name="owner/repository", health="healthy")
    connection = SimpleNamespace(organization_id=org, provider="github", status="connected")
    agent = SimpleNamespace(id=aid, name="Worker", status="active")
    session: Any = SimpleNamespace(get=AsyncMock(side_effect=[row, connection]),
        scalar=AsyncMock(return_value=agent), scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: ["policy"])))
    legacy = AsyncMock(return_value={"resources": []})
    monkeypatch.setattr(governance, "_catalog", legacy)
    monkeypatch.setattr(IntegrationFoundation, "resource_capabilities", AsyncMock(return_value=["github.repository.contents.read"]))
    monkeypatch.setattr(governance, "_profile", AsyncMock(return_value={"activeScopes": ["github.repository.contents.read"]}))
    return session, org, rid, aid, row, connection, legacy


@pytest.mark.asyncio
@pytest.mark.parametrize("effect, outcome", [("allow", "ALLOW"), ("deny", "DENY"), ("require_approval", "REQUIRE_APPROVAL")])
async def test_new_resource_identity_reaches_org_policy_without_legacy_connection_alias(resource_setup, monkeypatch, effect, outcome):
    session, org, rid, aid, _, _, legacy = resource_setup
    monkeypatch.setattr(governance, "_policy_public", AsyncMock(return_value={
        "id": "policy", "name": "Exact resource", "revision": 1, "effect": effect, "priority": 100,
        "selectors": {"agentIds": [str(aid)], "resourceIds": [str(rid)],
            "scopes": ["github.repository.contents.read"], "actions": [], "risks": []},
    }))
    decision = await governance._evaluate(session, org, _system_principal(org),
        agent_id=aid, resource_id=str(rid), scope="github.repository.contents.read")
    assert decision["outcome"] == outcome
    assert decision["preconditions"]["scopeAvailable"] is True
    assert decision["request"]["resourceId"] == str(rid)
    legacy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["other_tenant", "wrong_connection_tenant", "degraded", "revoked_scope"])
async def test_new_resources_fail_closed(resource_setup, monkeypatch, condition):
    session, org, rid, aid, row, connection, legacy = resource_setup
    if condition == "other_tenant":
        row.organization_id = uuid4()
    elif condition == "wrong_connection_tenant":
        connection.organization_id = uuid4()
    elif condition == "degraded":
        row.health = "unavailable"
    else:
        monkeypatch.setattr(IntegrationFoundation, "resource_capabilities", AsyncMock(return_value=[]))
    decision = await governance._evaluate(session, org, _system_principal(org),
        agent_id=aid, resource_id=str(rid), scope="github.repository.contents.read")
    assert decision["outcome"] == "DENY"
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_risk_assessment_uses_same_new_resource_identity(resource_setup):
    session, org, rid, aid, _, _, legacy = resource_setup
    principal = SimpleNamespace(permissions={"risk.read"})
    assessment = await governance._assess_risk(session, org, cast(HumanPrincipal, principal),
        {"agentId": str(aid), "resourceId": str(rid), "scope": "github.repository.contents.read"}, persist=False)
    assert assessment["context"]["resourceId"] == str(rid)
    assert assessment["context"]["scope"] == "github.repository.contents.read"
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_keeps_authorized_tools_for_new_resource_ids(resource_setup, monkeypatch):
    from app.runtime import managed

    session, org, rid, aid, row, connection, _ = resource_setup
    session.get.side_effect = [None, row, connection]
    monkeypatch.setattr(governance, "_policy_public", AsyncMock(return_value={
        "id": "policy", "name": "Exact resource", "revision": 1, "effect": "allow", "priority": 100,
        "selectors": {"agentIds": [str(aid)], "resourceIds": [str(rid)],
            "scopes": ["github.repository.contents.read"], "actions": [], "risks": []},
    }))
    executor = object.__new__(managed.ManagedRuntimeExecutor)
    executor._session, executor._registry = session, ProviderRegistry((ExpandedGitHubProvider(),))
    executor._active_scopes = AsyncMock(return_value=frozenset({"github.repository.contents.read"}))
    executor._execution_catalog = AsyncMock(return_value={"resources": [{"id": str(rid),
        "provider": "github", "status": "connected", "displayName": "owner/repository",
        "actions": [{"scope": "github.repository.contents.read"}]}]})
    tools = await executor._planner_tools(item=cast(WorkItem, SimpleNamespace(id=uuid4(), organization_id=org)),
        revision=cast(JobRevision, SimpleNamespace(definition={"requiredCapabilities": ["github.repository.contents.read"]})),
        agent=cast(AgentIdentity, SimpleNamespace(id=aid)))
    assert [(tool["resourceId"], tool["scope"]) for tool in tools] == [(str(rid), "github.repository.contents.read")]
    assert executor._capability_exclusions == []


@pytest.mark.asyncio
async def test_flag_off_keeps_legacy_catalog(resource_setup, monkeypatch):
    session, org, rid, _, _, _, legacy = resource_setup
    monkeypatch.setattr(settings, "integration_foundation_enabled", False)
    legacy.return_value = {"resources": [{"id": str(rid), "provider": "github"}]}
    assert await governance._live_policy_resource(session, org, str(rid)) == legacy.return_value["resources"][0]
    session.get.assert_not_awaited()


@pytest.mark.parametrize("status, outcome", [("attention", "blocked"), ("attention", "partial"), ("completed", "completed")])
def test_result_message_preserves_outcome_instead_of_hardcoding_success(status, outcome):
    reference = live_result_reference(result=cast(Result, SimpleNamespace(id=uuid4(), title="Task result", status=status)),
        run=cast(Run, SimpleNamespace(id=uuid4())), item=cast(WorkItem, SimpleNamespace(id=uuid4(), payload={"runtime": {"executionOutcome": outcome}})),
        job=cast(Job, SimpleNamespace(id=uuid4())), worker=cast(Worker, SimpleNamespace(id=uuid4())), summary="Actual outcome")
    assert reference["presentation"] == "live_result"
    assert reference["status"] == status
    assert reference["executionOutcome"] == outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("completed_count, requested_status, expected_status, outcome", [
    (0, "completed", "attention", "blocked"),
    (1, "attention", "attention", "partial"),
    (1, "completed", "completed", "completed"),
])
async def test_runtime_publishes_result_for_blocked_partial_and_successful_work(
    monkeypatch, completed_count, requested_status, expected_status, outcome,
):
    from app.infrastructure.database.models import Result, ResultVersion
    from app.runtime import managed

    saved = []

    async def flush():
        for row in saved:
            if row.id is None:
                row.id = uuid4()

    session: Any = SimpleNamespace(add=saved.append, flush=flush, commit=AsyncMock(),
        get=AsyncMock(return_value=SimpleNamespace(definition={"requiredCapabilities": ["github.repository.contents.read"]})))
    monkeypatch.setattr(managed, "WorkerMemoryService", lambda session: SimpleNamespace(
        record_operational=AsyncMock(), record_episode=AsyncMock()))
    monkeypatch.setattr(managed.TransactionalOutbox, "enqueue", AsyncMock())
    publish = AsyncMock()
    monkeypatch.setattr(managed, "append_live_result_message", publish)
    executor = object.__new__(managed.ManagedRuntimeExecutor)
    executor._session = session
    executor._close_browser_session = AsyncMock()
    executor._apply_stop_after_next_run = AsyncMock()
    org = uuid4()
    item = SimpleNamespace(id=uuid4(), organization_id=org, job_revision_id=uuid4(),
        correlation_id="test", payload={"integrationOrigin": {"threadId": str(uuid4())}})
    run = SimpleNamespace(id=uuid4())
    job = SimpleNamespace(id=uuid4(), name="Coding task")
    worker = SimpleNamespace(id=uuid4(), agent_identity_id=uuid4())
    steps = [SimpleNamespace(kind="action", status="completed", input={"scope": "github.repository.contents.read"},
        output={"summary": "Inspected repository"}) for _ in range(completed_count)]
    result = await executor._complete(cast(WorkItem, item), cast(Run, run), cast(Job, job), cast(Worker, worker), cast(list[RunStep], steps),
        completion_summary="Actual outcome", result_status=requested_status)
    assert result.state == "completed"  # reporting finishes normally and still delivers a result
    assert next(row for row in saved if isinstance(row, Result)).status == expected_status
    body = next(row for row in saved if isinstance(row, ResultVersion)).body
    assert body["executionOutcome"] == outcome
    assert body["completionCriteriaVerified"] is (expected_status == "completed")
    publish.assert_awaited_once()


def test_planner_can_report_partial_work_as_a_result_without_claiming_success():
    from app.runtime.planner.adaptive import AdaptiveRuntimePlanner

    planner = AdaptiveRuntimePlanner(cast(AIGateway, SimpleNamespace()))
    decision = planner._validate({"decision": "finish", "resultStatus": "attention",
        "summary": "Inspected files. Tests could not run.", "title": "Partial outcome",
        "instruction": "Explain the remaining blocker"},
        tools=[{"resourceId": "repo", "scope": "github.repository.contents.read"}],
        observations=[{"scope": "github.repository.contents.read", "summary": "Read the requested file"}],
        latest_browser=None)
    assert decision.decision == "finish"
    assert decision.result_status == "attention"


@pytest.mark.asyncio
async def test_preparation_reuses_reads_but_execution_rechecks_current_policy(resource_setup, monkeypatch):
    session, org, rid, aid, _, _, _ = resource_setup
    resource = {"displayName": "repo", "status": "connected", "actions": [
        {"scope": "read", "action": "read", "target": "repository", "risk": "low"},
        {"scope": "write", "action": "write", "target": "repository", "risk": "low"},
    ]}
    live = AsyncMock(return_value=resource)
    profile = AsyncMock(return_value={"activeScopes": ["read", "write"]})
    public = AsyncMock(return_value={"id": "p", "name": "Policy", "revision": 1,
        "effect": "allow", "priority": 100, "selectors": {}})
    monkeypatch.setattr(governance, "_live_policy_resource", live)
    monkeypatch.setattr(governance, "_profile", profile)
    monkeypatch.setattr(governance, "_policy_public", public)
    cache = {}
    for scope in ("read", "write"):
        decision = await governance._evaluate(session, org, _system_principal(org),
            agent_id=aid, resource_id=str(rid), scope=scope, preparation_cache=cache)
        assert decision["outcome"] == "ALLOW"
    assert session.scalar.await_count == session.scalars.await_count == 1
    live.assert_awaited_once()
    profile.assert_awaited_once()
    public.assert_awaited_once()
    # Unknown capabilities remain denied even with a warm preparation snapshot.
    assert (await governance._evaluate(session, org, _system_principal(org),
        agent_id=aid, resource_id=str(rid), scope="delete", preparation_cache=cache))["outcome"] == "DENY"
    public.return_value = {**public.return_value, "effect": "deny", "revision": 2}
    assert (await governance._evaluate(session, org, _system_principal(org),
        agent_id=aid, resource_id=str(rid), scope="write"))["outcome"] == "DENY"
    assert session.scalar.await_count == session.scalars.await_count == 2
