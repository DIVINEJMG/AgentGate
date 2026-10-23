"""Phase 3 conversation and preparation: no transaction across a model call (F17), retry of a saved
message (F12), the failure log (F18), the event rule (F22), declining a pending question (F23),
branch and pull-request grants (F19) and the compacted context (F4)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.services.conversation_commands import ConversationCommandCompiler
from app.application.services.conversation_semantics import reply_facts, validate_route_proposal
from app.application.services.conversations import ConversationService
from audoryn_shared.application.context.conversation import compact_context
from audoryn_shared.application.context.preparation import preparation_resources
from audoryn_shared.application.services.ai_gateway import ReleasingGateway
from audoryn_shared.domain.ai.providers import AIProviderError
from audoryn_shared.domain.conversation.intent import WorkerCommandIntent
from audoryn_shared.execution.providers.native.github.safety import (
    grounded_constraints,
    task_branch_prefix,
    validate_task_branch,
)
from audoryn_shared.observability.frames import app_frames


async def test_no_transaction_is_open_while_a_model_answers() -> None:  # F17
    order: list[str] = []
    session = SimpleNamespace(commit=AsyncMock(side_effect=lambda: order.append("commit")))

    class Gateway:
        async def generate_structured(self, **_: Any) -> dict[str, object]:
            order.append("model")
            return {}

        async def generate_validated_structured(self, **_: Any) -> dict[str, object]:
            order.append("model")
            return {}

        def with_workload(self, *_: Any) -> Gateway:
            return self

    gateway = ReleasingGateway(Gateway(), session)
    await gateway.generate_structured(prompt="x")
    await gateway.generate_validated_structured(prompt="x")
    await gateway.with_workload("complex").generate_structured(prompt="x")
    assert order == ["commit", "model"] * 3
    assert not hasattr(ReleasingGateway(object(), session), "generate_reviewed_structured")


async def test_a_saved_message_can_be_retried_once_it_has_no_reply() -> None:  # F12
    thread, message = SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4(), role="human")
    message.thread_id = thread.id
    session = SimpleNamespace(get=AsyncMock(return_value=message), scalar=AsyncMock(return_value=None))
    service = ConversationService(cast(Any, session))
    service.get_thread = AsyncMock(return_value=(thread, None))  # type: ignore[method-assign]
    service._reply = AsyncMock(return_value=("reply", "receipt"))  # type: ignore[method-assign]
    principal = SimpleNamespace(organization_id="org")
    assert await service.retry_message(organization_id=cast(Any, "org"), principal=cast(Any, principal),
                                       thread_id=thread.id, message_id=message.id) == (message, "reply", "receipt")
    session.scalar.return_value = uuid4()  # a command already answered it
    with pytest.raises(ValueError, match="already has a reply"):
        await service.retry_message(organization_id=cast(Any, "org"), principal=cast(Any, principal),
                                    thread_id=thread.id, message_id=message.id)


def test_an_unworkable_message_gets_a_plain_reply() -> None:  # F12
    error = AIProviderError("invalid_provider_response", "x", retryable=False)
    message = ConversationService(cast(Any, None))._provider_message(error)
    assert message.startswith("I couldn't work out what to do with that message") and "retry it" in message


def test_the_failure_log_names_agentgate_lines() -> None:  # F18
    with pytest.raises(ValueError) as caught:
        validate_task_branch("main", "main", uuid4())  # fails inside app/execution/.../safety.py
    shown = app_frames(caught.value)
    assert shown.startswith("execution/providers/native/github/safety.py:") and "validate_task_branch" in shown
    assert "tests/" not in shown and "site-packages" not in shown


CONTEXT = {"activeJobs": [{"id": "j1", "name": "Summarise new issues", "runs": "each time the github event issues.opened happens"},
                          {"id": "j2", "name": "Daily digest", "runs": "on its schedule"}]}


def test_an_existing_event_job_can_be_the_answer() -> None:  # F22
    facts = reply_facts(CONTEXT)
    assert facts["job:j1:event"].startswith("The job “Summarise new issues” already runs each time")
    assert "job:j2:event" not in facts
    answer = {"family": "conversation.answer", "requested_start": "on_event",
              "reply": {"answer": "That job already does it.", "factIds": ["job:j1:event"]}}
    validate_route_proposal(answer, facts)  # accepted on the first call
    with pytest.raises(AIProviderError, match="cite its job fact"):
        validate_route_proposal({**answer, "reply": {"answer": "Covered.", "factIds": []}}, facts)


async def test_declining_a_pending_question_closes_it_and_creates_nothing() -> None:  # F23
    pending = SimpleNamespace(status="clarification_required", receipt={"message": "Keep that one, or add a second?"})
    session = SimpleNamespace(scalar=AsyncMock(return_value=pending))
    compiler = ConversationCommandCompiler(cast(Any, session), cast(Any, None))
    intent = WorkerCommandIntent(family="conversation.answer", arguments={"resumeCommandId": str(uuid4())})
    validate_route_proposal(intent.model_dump(mode="json"))  # a chat answer may close a question
    closed = await compiler._close_declined(organization_id=uuid4(), principal=SimpleNamespace(user_id=uuid4()),
                                            thread=SimpleNamespace(id=uuid4()), intent=intent)
    assert pending.status == "cancelled" and "resumeCommandId" not in closed.arguments


def test_only_branches_and_pull_requests_the_person_named_are_granted() -> None:  # F19
    said = "merge codex/55ea0379/readme-setup in DIVINEJMG/school (draft pull request #8)"
    proposed = {"branches": ["codex/55ea0379/readme-setup", "dev"], "pullRequests": [8, 9]}
    assert grounded_constraints(proposed, said) == {"branches": ["codex/55ea0379/readme-setup"], "pullRequests": [8]}
    assert grounded_constraints({"branches": ["dev"]}, "merge into develop") == {"branches": []}
    item = uuid4()
    validate_task_branch("codex/55ea0379/readme-setup", "main", item, {"pullRequests": [8]}, pull_request=8)
    with pytest.raises(ValueError, match="branch or pull request the person named"):
        validate_task_branch("codex/55ea0379/readme-setup", "main", item, {"pullRequests": [8]}, pull_request=9)
    with pytest.raises(ValueError, match="default/protected"):
        validate_task_branch("main", "main", item, {"pullRequests": [8]}, pull_request=8)


def test_a_task_writes_to_its_audoryn_branches_and_keeps_its_older_codex_ones() -> None:
    item = uuid4()
    own = str(item)[:8]
    assert task_branch_prefix(item) == f"audoryn/{own}/"
    validate_task_branch(f"audoryn/{own}/fix", "main", item)
    validate_task_branch(f"codex/{own}/fix", "main", item)  # made before the rename
    for other in ("audoryn/00000000/fix", "codex/00000000/fix", "feature/fix"):
        with pytest.raises(ValueError, match="branch prefix"):
            validate_task_branch(other, "main", item)


def test_the_context_is_references_not_pasted_bodies() -> None:  # F4
    messages = [{"id": str(index), "role": "human" if index % 2 else "worker", "content": f"turn {index} " + "x" * 400}
                for index in range(20)]
    context = {
        "thread": {"id": "t", "messages": messages},
        "policiesAndApprovalBoundaries": [{"id": f"p{index}", "name": f"Integration binding {index}", "selectors": {"scopes": ["a"] * 13}}
                                          for index in range(10)] + [{"id": "mine", "name": "Ask before posting", "effect": "require_approval"}],
        "recentResults": [{"id": f"r{index}", "workerId": "w", "summary": f"result {index}"} for index in range(15)],
        "relevantMemories": [{"id": "m1", "content": "result 0"}, {"id": "m2", "content": "the team prefers drafts"}],
        "activeJobs": [{"id": "j", "workerId": "w", "name": "Job", "objective": "Job", "requiredCapabilities": ["a"] * 40,
                        "runs": "on its schedule"}],
    }
    compact = compact_context(context)
    thread = cast(dict[str, Any], compact["thread"])
    assert len(thread["messages"]) == 8 and len(thread["earlierTurns"]) == 12
    assert thread["earlierTurns"][0].startswith("worker: turn 0") and len(thread["earlierTurns"][0]) < 180
    assert compact["policiesAndApprovalBoundaries"] == [
        {"id": "mine", "name": "Ask before posting", "effect": "require_approval"},
        {"name": "10 resource grants made for earlier tasks", "effect": "allow"}]
    assert len(cast(list[Any], compact["recentResults"])) == 6
    assert [memory["id"] for memory in cast(list[Any], compact["relevantMemories"])] == ["m2"]  # m1 repeats a result
    assert compact["activeJobs"] == [{"id": "j", "name": "Job", "runs": "on its schedule"}]


def test_preparation_lists_each_resource_once_with_its_tool_difference() -> None:  # F4
    capabilities = {"github": {f"github.s{index}": "a tool" for index in range(84)}}
    every = [f"github.s{index}" for index in range(84)]
    catalog = [{"id": "r1", "name": "school", "provider": "github", "capabilities": every, "connectionId": "c"},
               {"id": "r2", "name": "docs", "provider": "github", "capabilities": every[:80]}]
    shown = preparation_resources(catalog, capabilities)
    assert shown[0] == {"id": "r1", "name": "school", "provider": "github", "tools": "all"}
    assert shown[1]["missingTools"] == ["github.s80", "github.s81", "github.s82", "github.s83"]
