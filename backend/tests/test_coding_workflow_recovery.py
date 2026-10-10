import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.bootstrap.settings import settings
from app.domain.actions.gateway import ActionProposal
from app.execution.coding import service
from app.execution.contracts import ExecutionProviderError
from app.execution.provider_executor import UniversalProviderExecutor
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.registry import ProviderRegistry
from app.runtime.evidence import bounded_evidence
from app.runtime.managed import ManagedRuntimeExecutor
from app.runtime.planner.adaptive import AdaptiveRuntimePlanner, _json_for_prompt


def test_read_prerequisites_remain_on_selected_repository_without_extra_writes():
    provider = ExpandedGitHubProvider()
    scopes = provider.task_prerequisites(["github.repository.tree.read"])
    assert set(scopes) == {
        "github.repository.tree.read",
        "github.repository.branch.read",
        "github.repository.metadata.read",
    }
    assert provider.task_prerequisites(scopes) == scopes


@pytest.mark.parametrize(
    "payload",
    [{"sha": "main"}, {"sha": "a" * 40, "recursive": "yes"}, {"sha": "a" * 40, "unknown": 1}],
)
def test_planner_rejects_full_schema_violations_before_checkpoint(payload):
    provider = ExpandedGitHubProvider()
    schema = provider.actions["repository.tree.read"].descriptor().input_schema
    raw = {
        "decision": "act",
        "summary": "Inspect",
        "title": "Inspect",
        "instruction": "Read tree",
        "scope": "github.repository.tree.read",
        "resourceId": "repo",
        "input": payload,
    }
    with pytest.raises(RuntimeError, match="constraint"):
        AdaptiveRuntimePlanner(cast(Any, SimpleNamespace()))._validate(
            raw,
            tools=[{"scope": raw["scope"], "resourceId": "repo", "inputSchema": schema}],
            observations=[],
            latest_browser=None,
        )


@pytest.mark.asyncio
async def test_prepare_invalid_sha_is_controlled_and_never_dispatches():
    provider = ExpandedGitHubProvider()
    loader = SimpleNamespace(load=AsyncMock(return_value=SimpleNamespace()))
    executor = UniversalProviderExecutor(
        registry=ProviderRegistry((provider,)), context_loader=cast(Any, loader)
    )
    proposal = ActionProposal(
        organization_id=uuid4(),
        agent_id=uuid4(),
        provider="github",
        operation="repository.tree.read",
        scope="github.repository.tree.read",
        resource_id="repo",
        payload={"sha": "main"},
        correlation_id="test",
        idempotency_key="test",
    )
    with pytest.raises(ExecutionProviderError) as caught:
        await executor.prepare(proposal)
    assert caught.value.error.code == "validation_error"
    assert not caught.value.error.retryable


def test_evidence_retains_structure_and_marks_limits():
    evidence = bounded_evidence({"sha": "a" * 40, "exitCode": 1, "content": "x" * 10000})
    assert evidence["data"]["content"] == "x" * 10000
    assert not evidence["evidenceLimits"]["truncated"]
    limited = json.loads(_json_for_prompt({"sha": "a" * 40, "content": "x" * 50000}, 4000))
    assert limited["data"]["sha"] == "a" * 40
    assert limited["evidenceLimits"]["truncated"]
    assert len(json.dumps(limited)) <= 4000


@pytest.mark.asyncio
async def test_planner_repairs_bad_sha_by_selecting_observed_branch_reader():
    provider = ExpandedGitHubProvider()
    raw = {
        "decision": "act",
        "resultStatus": "completed",
        "summary": "Inspect",
        "title": "Inspect",
        "instruction": "Inspect revision",
        "scope": "github.repository.tree.read",
        "resourceId": "repo",
        "input": {"sha": "main"},
    }
    repair = {**raw, "scope": "github.repository.branch.read", "input": {"branch": "main"}}
    gateway = SimpleNamespace(generate_structured=AsyncMock(side_effect=[raw, repair]))
    tools = [
        {
            "resourceId": "repo",
            "scope": provider.actions[operation].scope,
            "inputSchema": provider.actions[operation].descriptor().input_schema,
        }
        for operation in ("repository.tree.read", "repository.branch.read")
    ]
    decision = await AdaptiveRuntimePlanner(cast(Any, gateway)).choose_next(
        job={"id": "job", "instructions": "Inspect the requested repository"},
        worker={"id": "worker"},
        trigger={},
        tools=tools,
        observations=[{"providerOutput": {"defaultBranch": "main"}}],
        action_count=1,
        max_actions=10,
    )
    assert decision.scope == "github.repository.branch.read"
    assert gateway.generate_structured.await_count == 2


def test_planner_validates_nested_input_constraints():
    schema = {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["changes"],
    }
    raw = {
        "decision": "act",
        "summary": "Edit",
        "title": "Edit",
        "instruction": "Edit",
        "scope": "github.repository.commit.create",
        "resourceId": "repo",
        "input": {"changes": [{"content": 1}]},
    }
    with pytest.raises(RuntimeError, match="changes.0.content"):
        AdaptiveRuntimePlanner(cast(Any, SimpleNamespace()))._validate(
            raw,
            tools=[{"scope": raw["scope"], "resourceId": "repo", "inputSchema": schema}],
            observations=[],
            latest_browser=None,
        )


@pytest.mark.asyncio
async def test_invalid_saved_step_replans_and_exhaustion_returns_attention(monkeypatch):
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    monkeypatch.setattr(settings, "runtime_provider_retry_limit", 1)
    session = SimpleNamespace(
        commit=AsyncMock(), scalars=AsyncMock(return_value=SimpleNamespace(all=list))
    )
    executor = object.__new__(ManagedRuntimeExecutor)
    executor._session = cast(Any, session)
    executor._complete = AsyncMock(return_value="attention-result")
    monkeypatch.setattr("app.runtime.managed.notify_integration_work", AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr(
        "app.runtime.managed.TransactionalOutbox", lambda _: SimpleNamespace(enqueue=enqueue)
    )
    item = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        job_id=uuid4(),
        correlation_id="test",
        payload={"integrationOrigin": {}, "runtime": {}},
    )
    item.payload["integrationOrigin"] = {"conversationId": "origin"}
    run = SimpleNamespace(id=uuid4(), status="running")
    step = SimpleNamespace(status="pending", output={})
    error = ExecutionProviderError(
        code="validation_error",
        retryable=False,
        provider="github",
        operation="repository.tree.read",
        correlation_id="test",
        safe_message="Invalid revision",
    )
    action = AsyncMock(side_effect=error)
    args = {
        "item": item,
        "run": run,
        "step": step,
        "current_step": 1,
        "job": SimpleNamespace(),
        "worker": SimpleNamespace(),
    }
    outcome = await executor._integration_guarded_action(action, **args)
    assert outcome.state == "continue"
    assert step.status == "failed"
    assert item.payload["runtime"]["currentStep"] == 2
    enqueue.assert_awaited_once()
    assert await executor._integration_guarded_action(action, **args) == "attention-result"
    assert executor._complete.call_args.kwargs["result_status"] == "attention"


@pytest.mark.asyncio
async def test_command_exit_is_committed_before_failed_export(monkeypatch):
    command = SimpleNamespace(
        id=uuid4(),
        session_id=uuid4(),
        organization_id=uuid4(),
        status="running",
        process_id=4,
        command="python -m pytest",
        output={"startFingerprint": "old"},
    )
    row = SimpleNamespace(id=command.session_id, sandbox_id="sandbox")
    request = SimpleNamespace(
        input={"commandId": str(command.id)}, organization_id=command.organization_id
    )
    commits = []

    async def commit():
        commits.append(dict(command.output))

    session = SimpleNamespace(get=AsyncMock(return_value=command), commit=commit)
    runtime = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(stdout="test output")),
        command_result=AsyncMock(return_value={"exitCode": 1}),
    )
    monkeypatch.setattr(service, "export", AsyncMock(side_effect=ValueError("Unsafe export")))
    output = await service.inspect_command(session, runtime, row, request, False, {})
    assert next(saved for saved in commits if "exitCode" in saved)["exitCode"] == 1
    assert output["status"] == "completed"
    assert output["exitCode"] == 1
    assert output["sourceVerificationAvailable"] is False
    assert output["sourceUnchangedDuringCommand"] is False
    # Reinspection returns persisted evidence and never launches the command again.
    assert (await service.inspect_command(session, runtime, row, request, False, {}))[
        "exitCode"
    ] == 1


@pytest.mark.asyncio
async def test_diff_preserved_but_unauthorized_source_cannot_publish(monkeypatch):
    row = SimpleNamespace(
        sandbox_id="sandbox", base_sha="a" * 40, evidence={"baselineArtifactId": "baseline"}
    )
    runtime = SimpleNamespace(
        write=AsyncMock(),
        run=AsyncMock(
            return_value=SimpleNamespace(
                exit_code=0, stdout=json.dumps({"allowed.py": "ok", "outside.py": "change"})
            )
        ),
    )
    monkeypatch.setattr(
        service, "load_artifact", AsyncMock(return_value=json.dumps({"allowed.py": "ok"}))
    )
    monkeypatch.setattr(
        service, "store_task_artifact", AsyncMock(return_value={"artifactId": "diff"})
    )
    output = await service.export(
        runtime, row, SimpleNamespace(), {"constraints": {"allowedPaths": ["allowed.py"]}}
    )
    assert output["publicationAllowed"] is False
    assert output["changes"][0]["path"] == "outside.py"
    assert "outside.py" in output["diff"]


def test_export_ignores_generated_caches_but_preserves_tracked_cache_files(tmp_path):
    root = tmp_path / "workspace"
    cache = root / ".pytest_cache"
    cache.mkdir(parents=True)
    (cache / "generated").write_text("temporary")
    (root / "source.py").write_text("source")
    baseline = tmp_path / "baseline.json"
    script = service.EXPORT_SCRIPT.replace(
        "root='/workspace'", "root=" + repr(str(root).replace("\\", "/"))
    ).replace("/tmp/audoryn-baseline-paths.json", str(baseline).replace("\\", "/"))
    baseline.write_text("[]")
    namespace = {}
    exec(script, namespace)  # noqa: S102 - execute the fixed export script against isolated test files
    assert ".pytest_cache/generated" not in namespace["result"]
    baseline.write_text(json.dumps([".pytest_cache/generated"]))
    namespace = {}
    exec(script, namespace)  # noqa: S102 - same fixed script with a tracked baseline
    assert namespace["result"][".pytest_cache/generated"] == "temporary"
