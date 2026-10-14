"""Improvement plan, phase 3a: an ordinary reply takes one AI call instead of two, under the same
checks. A reply that is missing or fails a check falls back to the separate reply call."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from app.application.services.conversation_commands import ConversationCommandCompiler
from app.application.services.conversation_semantics import REPLY_RULES, reply_from_intent
from app.application.services.intent_interpreter import IntentInterpreter
from app.domain.ai.providers import AIInvocationContext
from app.domain.conversation.intent import WorkerCommandIntent
from tests.test_conversation_semantics import selector

RUN = str(uuid4())
CONTEXT: dict[str, Any] = {
    "activeJobs": [{"id": "job", "name": "Daily report"}],
    "currentWork": [{"id": "item", "jobId": "job"}],
    "recentRuns": [{"id": RUN, "workItemId": "item", "status": "completed",
                    "steps": [{"kind": "action", "status": "completed"}]}],
    "thread": {"messages": []},
}
STATUS_FACT = f"run:{RUN}:status"


async def _turn(outputs: list[Any], message: str = "How did the daily report go?") -> tuple[str, list[Any]]:
    """One chat turn: interpretation, then the answer handler, against a scripted model."""
    calls: list[Any] = []
    gateway = selector({"model": outputs}, calls)
    intent = await IntentInterpreter(gateway).interpret(message=message, context=CONTEXT,
                                                        invocation_context=AIInvocationContext())
    compiler = ConversationCommandCompiler(cast(Any, SimpleNamespace()), gateway)
    receipt = await compiler._dispatch(
        organization_id=uuid4(), principal=cast(Any, SimpleNamespace()),
        thread=cast(Any, SimpleNamespace(id=uuid4(), worker_id=None)),
        source_message=cast(Any, SimpleNamespace(id=uuid4(), content=message)),
        intent=intent, context=CONTEXT, command=cast(Any, SimpleNamespace(id=uuid4())), confirmed=False)
    return receipt.message, calls


@pytest.mark.asyncio
async def test_a_reply_written_with_the_routing_decision_takes_one_call() -> None:
    answer, calls = await _turn([{"family": "conversation.answer",
                                  "reply": {"answer": "Here is the latest on the daily report.", "factIds": [STATUS_FACT]}}])
    assert len(calls) == 1
    # Backend facts are rendered by the backend, exactly as with a separate reply.
    assert answer == "Here is the latest on the daily report.\n\nThe task “Daily report” finished."


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    {"answer": "I have committed the changes.", "factIds": []},     # unsupported claim
    {"answer": "Done.", "factIds": ["run:invented:status"]},         # fact the backend did not give
])
async def test_a_reply_that_fails_a_check_falls_back_to_the_separate_reply(reply: dict[str, Any]) -> None:
    good = {"answer": "I can explain what the report covers.", "factIds": []}
    answer, calls = await _turn([{"family": "conversation.answer", "reply": reply}, good])
    assert answer == good["answer"] and len(calls) == 2
    assert "grounded_chat_reply_v1" in calls[1][1]


@pytest.mark.asyncio
async def test_without_a_reply_the_separate_reply_is_used_as_before() -> None:
    good = {"answer": "Hello! How can I help?", "factIds": []}
    answer, calls = await _turn([{"family": "conversation.answer"}, good], message="hello")
    assert answer == good["answer"] and len(calls) == 2


def test_a_reply_is_used_only_for_ordinary_answers() -> None:
    intent = WorkerCommandIntent.model_validate({"family": "worker.create", "reply": {"answer": "Sure", "factIds": []}})
    assert reply_from_intent(intent, CONTEXT) is None


@pytest.mark.asyncio
async def test_the_routing_call_is_given_the_reply_rules_and_backend_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application.services import conversation_semantics

    seen: dict[str, Any] = {}

    async def capture(gateway: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"family": "conversation.answer"}

    monkeypatch.setattr(conversation_semantics, "validated_structured", capture)
    await IntentInterpreter(cast(Any, None)).interpret(message="hello", context=CONTEXT,
                                                       invocation_context=AIInvocationContext())
    assert REPLY_RULES in seen["system"]
    assert "BACKEND_FACTS" in seen["prompt"] and STATUS_FACT in seen["prompt"]
