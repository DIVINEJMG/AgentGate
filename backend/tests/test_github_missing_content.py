from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_github_expanded import request

from app.application.services.task_activity import action_phase
from app.bootstrap.settings import settings
from app.domain.actions.gateway import ActionProposal
from app.execution.contracts import ExecutionProviderError
from app.execution.provider_executor import UniversalProviderExecutor
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.native.github.transport import GitHubResponse
from app.execution.providers.native.http import ProviderTransportError
from app.runtime.managed import ManagedRuntimeExecutor

SHA = "a" * 40


def not_found():
    return ProviderTransportError(code="resource_not_found", retryable=False, safe_message="Not found")


class HTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def request(self, **kwargs):
        self.calls.append(kwargs)
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return GitHubResponse(response, "request", 200)


async def execute(replies):
    http = HTTP(replies)
    provider = ExpandedGitHubProvider(http=http)
    result = await provider.execute(request=request(provider, "repository.contents.read", {
        "path": "new/folder/file.py", "ref": "main",
    }), configuration={"repository": "owner/repo"}, credential="test")
    return result, http


async def test_confirmed_absence_is_verified_path_evidence_not_task_failure():
    result, http = await execute([not_found(), {"default_branch": "main"}, {"sha": SHA}, not_found()])
    assert result.output["exists"] is False
    assert result.output["path"] == "new/folder/file.py"
    assert result.output["revision"] == SHA
    assert result.verification.verified
    assert "no content was read or created" in result.verification.summary
    assert http.calls[-1]["url"].endswith("/contents/new/folder/file.py?ref=" + SHA)
    assert all(call["method"] == "GET" for call in http.calls)


@pytest.mark.parametrize("replies", [
    [not_found(), not_found()],
    [not_found(), {"default_branch": "main"}, not_found()],
    [ProviderTransportError(code="authorization_error", retryable=False, safe_message="Denied")],
])
async def test_unknown_access_revision_and_permission_errors_never_claim_absence(replies):
    with pytest.raises(ExecutionProviderError):
        await execute(replies)


async def test_branch_race_returns_content_from_observed_sha():
    result, _ = await execute([not_found(), {"default_branch": "main"}, {"sha": SHA},
                              {"path": "new/folder/file.py", "content": "YWJj", "encoding": "base64"}])
    assert result.output["content"] == "YWJj"
    assert result.output.get("exists") is not False


async def test_existing_content_requires_no_absence_probes():
    _, http = await execute([{"path": "new/folder/file.py", "content": "YWJj"}])
    assert len(http.calls) == 1


@pytest.mark.parametrize("code,operation,expected", [
    ("resource_not_found", "repository.contents.read", "plan"),
    ("authorization_error", "repository.contents.read", "failed"),
    ("resource_not_found", "repository.branch.create", "failed"),
    ("uncertain_outcome", "repository.contents.read", "wait"),
])
async def test_dispatched_read_error_reaches_outer_replanning_handler(monkeypatch, code, operation, expected):
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    error = ExecutionProviderError(code=code, retryable=False, provider="github",
        operation=operation, correlation_id="test", safe_message="Revision inaccessible")
    monkeypatch.setattr("app.runtime.managed.ActionGateway", lambda *args: SimpleNamespace(
        execute_request=AsyncMock(side_effect=error)))
    enqueue = AsyncMock()
    monkeypatch.setattr("app.runtime.managed.TransactionalOutbox", lambda _: SimpleNamespace(enqueue=enqueue))
    monkeypatch.setattr("app.runtime.managed.notify_integration_work", AsyncMock())
    executor = object.__new__(ManagedRuntimeExecutor)
    executor._session = cast(AsyncSession, SimpleNamespace(add=Mock(), flush=AsyncMock(), commit=AsyncMock()))
    executor._provider_executor = cast(UniversalProviderExecutor, SimpleNamespace())
    executor._active_scopes = AsyncMock(return_value=frozenset())
    executor._action_record_payload = lambda **kwargs: {"error": kwargs.get("error")}
    executor._fail = AsyncMock()
    executor._wait_integration = AsyncMock(return_value="waiting")
    item = SimpleNamespace(id=uuid4(), organization_id=uuid4(), job_id=uuid4(),
        correlation_id="test", status="running", payload={"integrationOrigin": {"messageId": "origin"}})
    run = SimpleNamespace(id=uuid4(), status="running")
    step = SimpleNamespace(status="pending", output={})
    proposal = ActionProposal(organization_id=item.organization_id, agent_id=uuid4(),
        provider="github", operation="repository.contents.read", scope="github.repository.contents.read",
        resource_id="repo", payload={"path": "file"}, correlation_id="test", idempotency_key="saved-read")
    outcome = await executor._integration_guarded_action(executor._execute_authorized,
        item=item, run=run, step=step, job=SimpleNamespace(), worker=SimpleNamespace(),
        agent=SimpleNamespace(id=proposal.agent_id), proposal=proposal,
        universal=SimpleNamespace(execution=request(ExpandedGitHubProvider(), operation, {"path": "file"})),
        decision={"outcome": "ALLOW", "reason": "Granted"}, current_step=1,
        existing=SimpleNamespace(id=uuid4(), status="processing", payload={}))
    if expected == "plan":
        assert outcome.state == "continue" and outcome.continuation_phase == "plan"
        assert step.output["data"]["integrationFailure"]["code"] == "resource_not_found"
        assert item.payload["runtime"]["currentStep"] == 2
        executor._fail.assert_not_awaited()
    elif expected == "wait":
        assert outcome == "waiting"
        executor._wait_integration.assert_awaited_once()
        executor._fail.assert_not_awaited()
    else:
        assert outcome.state == "failed"
        executor._fail.assert_awaited_once()


def test_progress_names_only_the_typed_path():
    label = action_phase("github.repository.contents.read", {"path": "one/file.py"})[1]
    assert "one/file.py" in label
