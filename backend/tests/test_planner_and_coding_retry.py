"""Planner: a left-out plan step is the current step (no correction call), and every rejection is
logged by its reason. Coding: workspace edits are safe to repeat, so a sandbox hiccup is retried
instead of pausing the task as an uncertain write."""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import pytest

from audoryn_execution.execution.integration_execution import execute_native_action
from audoryn_shared.execution.coding import service
from audoryn_shared.execution.contracts import ExecutionProviderError
from audoryn_shared.runtime.planner.adaptive import (
    AdaptiveRuntimePlanner,
    normalize_plan,
    rejection_reason,
)
from tests.test_integration_foundation import Coordination, DeterministicAdapter, execution
from tests.test_run_plans import ACT, SCOPES, TOOLS, step

# --- Planner -----------------------------------------------------------------------------------


def test_a_left_out_plan_step_is_the_current_step() -> None:
    attach = AdaptiveRuntimePlanner._with_plan
    saved = normalize_plan([step("Read a"), step("Write b", "bench.record.write")], scopes=SCOPES)
    saved[0]["status"] = "done"
    assert attach(ACT, {}, tools=TOOLS, plan=saved, plan_revisions_left=3).plan_step == 2
    # A step that is not pending is still sent back for correction.
    with pytest.raises(RuntimeError, match="Pending steps: \\[2\\]"):
        attach(ACT, {"planStep": 1}, tools=TOOLS, plan=saved, plan_revisions_left=3)


def test_rejection_reasons_are_short_first_sentences() -> None:
    assert rejection_reason("Set planStep to the number of the pending plan step. Pending steps: [2].") == (
        "Set planStep to the number of the pending plan step")
    assert len(rejection_reason("x" * 900)) == 200
    assert rejection_reason("Line one\nLine two") == "Line one"


class _Gateway:
    """Returns a proposal the planner's checks reject, then a valid one."""

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers

    async def generate_structured(self, **kwargs: Any) -> dict[str, Any]:
        return self.answers.pop(0)


@pytest.mark.asyncio
async def test_a_rejected_proposal_is_logged_with_its_reason(caplog: pytest.LogCaptureFixture) -> None:
    from audoryn_shared.domain.ai.providers import AIInvocationContext

    good = {"decision": "act", "summary": "s", "title": "Read a", "instruction": "i",
            "resourceId": TOOLS[0]["resourceId"], "scope": TOOLS[0]["scope"], "input": {"key": "a"}}
    bad = {**good, "scope": "bench.record.delete"}
    planner = AdaptiveRuntimePlanner(_Gateway([bad, good]))  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        decision = await planner.choose_next(
            job={"id": "job", "instructions": "Read a"}, worker={"id": "worker"}, trigger={},
            tools=TOOLS, observations=[], action_count=0, max_actions=10,
            invocation_context=AIInvocationContext(correlation_id="corr-1"))
    assert decision.scope == TOOLS[0]["scope"]
    [record] = [r for r in caplog.records if "Planner proposal rejected" in r.getMessage()]
    assert "call=1 correlation=corr-1 reason=`scope` 'bench.record.delete' is not allowed here" in record.getMessage()


# --- Coding ------------------------------------------------------------------------------------


def _workspace(error_code: str, retryable: bool) -> tuple[Any, Any, Any]:
    class Failing(DeterministicAdapter):
        async def execute(self, *, request: Any, **kw: Any) -> Any:
            self.calls += 1
            raise ExecutionProviderError(code=error_code, retryable=retryable, provider="github",  # type: ignore[arg-type]
                                         operation=request.operation, correlation_id="c", safe_message="Sandbox hiccup.")

    request, context = execution()
    request = replace(request, capability=replace(request.capability, target="workspace", mode="action"))
    return Failing(), request, context


@pytest.mark.asyncio
async def test_a_failed_workspace_edit_is_retried_not_paused() -> None:
    adapter, request, context = _workspace("temporary_provider_error", True)
    with pytest.raises(ExecutionProviderError) as failure:
        await execute_native_action(provider=adapter, request=request, context=context, coordinator=Coordination())
    assert failure.value.error.code == "temporary_provider_error" and failure.value.error.retryable
    assert adapter.reconciliations == 0


@pytest.mark.asyncio
async def test_workspace_commands_and_outside_writes_still_pause_for_checking() -> None:
    adapter, request, context = _workspace("uncertain_outcome", False)
    with pytest.raises(ExecutionProviderError) as failure:
        await execute_native_action(provider=adapter, request=request, context=context, coordinator=Coordination())
    assert failure.value.error.code == "uncertain_outcome"
    outside = DeterministicAdapter(fail=True)
    request, context = execution()  # a write that reaches another system
    with pytest.raises(ExecutionProviderError) as failure:
        await execute_native_action(provider=outside, request=request, context=context, coordinator=Coordination())
    assert failure.value.error.code == "uncertain_outcome" and outside.reconciliations == 1


class _Runtime:
    def __init__(self, failures: int) -> None:
        self.failures, self.writes = failures, 0

    async def write(self, sandbox_id: str, path: str, content: str) -> None:
        self.writes += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("502 Bad Gateway")


@pytest.mark.asyncio
async def test_the_request_file_upload_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(service.asyncio, "sleep", instant)
    runtime = _Runtime(failures=2)
    await service.write_control_file(runtime, "sb", "/tmp/audoryn-step.json", "{}")
    assert runtime.writes == 3
    with pytest.raises(RuntimeError):
        await service.write_control_file(_Runtime(failures=3), "sb", "/tmp/audoryn-step.json", "{}")


@pytest.mark.parametrize(("message", "label"), [
    ("Expected to receive information about written file", "missing_write_acknowledgement"),
    ("502 Bad Gateway from envd", "service_unavailable"),
    ("Request timed out", "timeout"),
    ("Connection reset by peer", "connection_error"),
    ("something else entirely", "unclassified_exception"),
])
def test_sandbox_errors_get_a_safe_label(message: str, label: str) -> None:
    assert service.failure_reason(RuntimeError(message)) == label
