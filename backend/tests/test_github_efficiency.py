from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.native.github.transport import GitHubHTTPClient
from tests.test_github_expanded import ScriptedHTTP, request


@pytest.mark.asyncio
async def test_api_only_reads_never_start_coding_runtime(monkeypatch):
    from app.execution.coding import service
    coding = AsyncMock(side_effect=AssertionError("API read must not use E2B"))
    monkeypatch.setattr(service, "execute_coding_action", coding)
    provider = ExpandedGitHubProvider(ScriptedHTTP([{"full_name": "owner/repo", "default_branch": "main"}]))
    await provider.execute(request=request(provider, "repository.metadata.read"),
                           configuration={"repository": "owner/repo"}, credential="fixture")
    coding.assert_not_awaited()


@pytest.mark.asyncio
async def test_immutable_read_cache_is_exact_and_authority_scoped():
    http = ScriptedHTTP([{"tree": []}, {"tree": []}, {"tree": []}, {"tree": []}])
    provider = ExpandedGitHubProvider(http)
    original = request(provider, "repository.tree.read", {"sha": "a" * 40})
    original = replace(original, resource=replace(original.resource, metadata={"authorityVersion": 1}))
    first = await provider.execute(request=original, configuration={"repository": "owner/repo"}, credential="fixture")
    await provider.execute(request=original, configuration={"repository": "owner/repo"}, credential="fixture")
    assert len(http.calls) == 1
    first.output["tree"].append({"path": "mutated"})
    cached = await provider.execute(request=original, configuration={"repository": "owner/repo"}, credential="fixture")
    assert cached.output["tree"] == []
    for changed in [replace(original, work_item_id=uuid4()),
                    replace(original, organization_id=uuid4()),
                    replace(original, resource=replace(original.resource, metadata={"authorityVersion": 2}))]:
        await provider.execute(request=changed, configuration={"repository": "owner/repo"}, credential="fixture")
    assert len(http.calls) == 4


@pytest.mark.asyncio
async def test_http_connection_scope_reuses_then_closes_client():
    creates = closes = 0
    client = object()

    @asynccontextmanager
    async def factory(**_):
        nonlocal creates, closes
        creates += 1
        try:
            yield client
        finally:
            closes += 1

    http = GitHubHTTPClient(client_factory=factory)
    async with http.execution_session():
        async with http.connection() as first, http.connection() as second:
            assert first is second is client
        assert creates == 1 and closes == 0
    assert closes == 1
    async with http.connection():
        assert creates == 2
    assert closes == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,budget,connection", [
    ("repository.workspace.open", 600, "github"), ("repository.metadata.read", 90, "github"),
    ("repository.metadata.read", 90, "gmail"), ("repository.metadata.read", 90, "slack"),
])
async def test_initialization_has_separate_timeout_and_matching_slots(monkeypatch, operation, budget, connection):
    from app.bootstrap.settings import settings
    from app.execution import integration_execution
    monkeypatch.setattr(settings, "coding_initialization_seconds", 600)
    monkeypatch.setattr(integration_execution, "initialization_budget", AsyncMock(return_value=600))
    observed = []

    @asynccontextmanager
    async def timer(seconds):
        observed.append(seconds)
        yield

    monkeypatch.setattr(integration_execution.asyncio, "timeout", timer)
    provider = ExpandedGitHubProvider(ScriptedHTTP([]))
    req = request(provider, operation, {"sha": "a" * 40} if "workspace" in operation else {})
    req = replace(req, capability=replace(req.capability, provider=connection), resource=replace(req.resource, provider=connection))
    coordination = SimpleNamespace(acquire_slot=AsyncMock(return_value="slot"),
        reserve_limit=AsyncMock(return_value=True), release_slot=AsyncMock())
    executor = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(output={})))
    context = SimpleNamespace(resource=SimpleNamespace(metadata={"connectionId": "connection"}), configuration={}, credential="fixture")
    await integration_execution.execute_native_action(provider=executor, request=req, context=context, coordinator=coordination)
    assert observed == [budget]
    assert all(call.kwargs["ttl_seconds"] == budget + 120 for call in coordination.acquire_slot.call_args_list)


@pytest.mark.asyncio
async def test_saved_initialization_deadline_bounds_retry_and_expiry(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.execution import integration_execution
    from app.execution.contracts import ExecutionProviderError
    from app.infrastructure.database import session as database
    provider = ExpandedGitHubProvider(ScriptedHTTP([]))
    req = request(provider, "repository.workspace.open", {"sha": "a" * 40})
    value = [(datetime.now(UTC) + timedelta(seconds=40)).isoformat(), "bundle_transfer", "creating"]

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: value)))

    monkeypatch.setattr(database, "session_factory", factory)
    assert 1 <= await integration_execution.initialization_budget(req) <= 40
    value[0] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ExecutionProviderError) as caught:
        await integration_execution.initialization_budget(req)
    assert not caught.value.error.retryable
    assert "bundle_transfer" in caught.value.error.safe_message
