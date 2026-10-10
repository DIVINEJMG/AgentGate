import logging
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.conversation_commands import ConversationCommandCompiler
from app.application.services.conversation_semantics import grounded_reply
from app.application.services.conversations import ConversationService
from app.application.services.intent_interpreter import IntentInterpreter
from app.application.services.reply_claims import unsupported_assertion
from app.domain.ai.providers import AIGateway, AIInvocationContext, AIProviderError
from app.domain.conversation.intent import WorkerCommandIntent
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.models import ConversationMessage, ConversationThread
from tests.test_conversation_semantics import selector


@pytest.mark.parametrize("answer", [
    "I will review whether the tests passed, using recorded results.",
    "Please review the tests and distinguish passed checks from analysis.",
    "Did the tests pass? Were the changes committed?",
    "If the tests passed, publication would be the next step.",
    "I will inspect the tests and explain whether the task completed.",
    "I have not inspected or committed changes.",
    "I did not claim that the tests passed.",
    "It is not true that the tests passed.",
    "I do not think the task completed.",
    'The log says "I have committed the changes." Verify that claim.',
    "The phrase ‘tests passed’ needs evidence.",
    '> The tests passed.\nThat quotation does not prove an outcome.',
    'Example: "GitHub tools are unavailable" is an unsupported limitation.',
    "Hello! What would you like to discuss?",
])
async def test_legitimate_prose_is_accepted_without_correction(answer):
    calls = []
    gateway = selector({"first": [{"answer": answer, "factIds": []}]}, calls)
    assert await grounded_reply(gateway, message="Discuss this", context={}, invocation_context=AIInvocationContext()) == answer
    assert len(calls) == 1


@pytest.mark.parametrize("answer,rule", [
    ("I have committed the changes.", "execution_assertion"),
    ("I inspected the repository.", "execution_assertion"),
    ("The tests passed.", "outcome_assertion"),
    ("I claim the tests passed.", "outcome_assertion"),
    ("The task has completed.", "outcome_assertion"),
    ("All work attempted has been preserved.", "preservation_assertion"),
    ("GitHub execution capabilities are not available in this environment.", "capability_assertion"),
    ("No changes have been made.", "absence_assertion"),
    ("I committed the changes, if that answers your question.", "execution_assertion"),
])
def test_direct_claims_still_require_backend_evidence(answer, rule):
    assert unsupported_assertion(answer) == rule


@pytest.mark.parametrize("family", ["worker.create", "integration.execute", "web.research", "attachment.analyze", "job.status", "conversation.answer"])
async def test_ai_operation_keeps_full_context_and_logs_only_safe_fields(family, caplog):
    calls = []
    context = {"allowedCapabilities": [{"scope": "fixture.scope", "schema": "PRIVATE-CONTEXT"}],
        "thread": {"messages": [{"content": "Earlier human instruction"}]},
        "relevantAttachments": [{"id": "attachment", "summary": "Untrusted evidence"}]}
    gateway = selector({"first": [{"family": family}]}, calls)
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        selected = await IntentInterpreter(gateway).interpret(message="Understand this in context", context=context,
            invocation_context=AIInvocationContext(correlation_id="fixture-correlation"))
    assert selected.family == family and len(calls) == 1
    assert "PRIVATE-CONTEXT" in calls[0][1] and "Untrusted evidence" in calls[0][1]
    assert f"operation={family}" in caplog.text and "context_profile=full_interpretation" in caplog.text
    assert "PRIVATE-CONTEXT" not in caplog.text and "Earlier human instruction" not in caplog.text


async def test_reply_fallback_preserves_stage_and_relevant_context(caplog):
    calls = []
    bad = {"answer": "I have committed the changes.", "factIds": []}
    good = {"answer": "I will review whether the tests passed.", "factIds": []}
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        answer = await grounded_reply(selector({"first": [bad, bad], "next": [good]}, calls),
            message="Discuss the review", context={"thread": {"messages": [{"content": "Relevant follow-up"}]},
                "allowedCapabilities": [{"schema": "PRIVATE-SCHEMA"}]}, invocation_context=AIInvocationContext())
    assert answer == good["answer"] and [model for model, _ in calls] == ["first", "first", "next"]
    assert all("Relevant follow-up" in prompt and "PRIVATE-SCHEMA" not in prompt for _, prompt in calls)
    assert "Repair only the ordinary reply" in calls[1][1]
    assert "Reinterpret the original" not in calls[1][1]
    assert "validation_rule=execution_assertion" in caplog.text
    assert bad["answer"] not in caplog.text


async def test_exhausted_validation_is_not_reported_as_outage(caplog):
    bad = {"answer": "The tests passed.", "factIds": []}
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"), pytest.raises(AIProviderError) as caught:
        await grounded_reply(selector({"first": [bad, bad], "next": [bad, bad]}, []), message="Discuss",
            context={}, invocation_context=AIInvocationContext())
    assert caught.value.validation_exhausted
    service = object.__new__(ConversationService)
    message = service._provider_message(caught.value)
    assert "response validation" in message and "unavailable" not in message and "outage" not in message
    assert "validation_failures=2" in caplog.text
    assert "origin=local_validation" in caplog.text


async def test_intent_correction_keeps_intent_contract():
    calls = []
    bad = {"family": "conversation.answer", "arguments": {"patch": "private patch"}}
    good = {"family": "integration.execute"}
    await IntentInterpreter(selector({"first": [bad, good]}, calls)).interpret(message="Request",
        context={}, invocation_context=AIInvocationContext())
    assert "Correct the WorkerCommandIntent" in calls[1][1]
    assert "chat_action_payload" in calls[1][1]
    assert "private patch" not in calls[1][1]
    assert "worker_command_intent_v1" in calls[1][1]


def test_validation_rule_cannot_expose_arbitrary_text():
    error = AIProviderError("invalid_provider_response", "private", retryable=False, validation_rule="SECRET-TOKEN")
    assert error.validation_rule is None


async def test_rejected_reply_does_not_leave_accepted_command():
    commands = []

    async def flush():
        for command in commands:
            if command.id is None:
                command.id = uuid4()

    session = SimpleNamespace(add=commands.append, flush=AsyncMock(side_effect=flush))
    compiler = ConversationCommandCompiler(cast(AsyncSession, session), cast(AIGateway, None))
    compiler._event = AsyncMock()
    compiler._dispatch = AsyncMock(side_effect=AIProviderError("invalid_provider_response", "PRIVATE-REPLY",
        retryable=False, rejection_reason="unsupported_claim", validation_rule="execution_assertion"))
    org = uuid4()
    with pytest.raises(AIProviderError):
        await compiler.compile_and_execute(organization_id=org, principal=cast(HumanPrincipal, SimpleNamespace(organization_id=org, user_id=uuid4())),
            thread=cast(ConversationThread, SimpleNamespace(organization_id=org, id=uuid4(), worker_id=uuid4())),
            source_message=cast(ConversationMessage, SimpleNamespace(id=uuid4())), intent=WorkerCommandIntent(family="conversation.answer"),
            authoritative_context={})
    assert commands[0].status == "unavailable"
    assert commands[0].payload["aiFailure"]["validationRule"] == "execution_assertion"
    assert "PRIVATE-REPLY" not in str(commands[0].receipt)
    assert compiler._event.await_args is not None
    assert compiler._event.await_args.args[0] == "conversation.command.completed"
