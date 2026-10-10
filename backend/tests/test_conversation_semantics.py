import json
import logging
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.application.services.ai_gateway import ProviderAIGateway
from app.application.services.conversation_semantics import (
    grounded_reply,
    ordinary_reply_context,
    reply_facts,
    validate_route_proposal,
)
from app.application.services.intent_interpreter import IntentInterpreter
from app.application.services.ordered_ai import OrderedTextGateway
from app.domain.ai.providers import (
    AIGateway,
    AIInvocationContext,
    AIProviderError,
    AIResponse,
    ModelProviderCapabilities,
)
from app.domain.ai.registry import ModelRegistry, ModelRoute


def selector(outputs, calls):
    class Provider:
        name = "fake"
        capabilities = ModelProviderCapabilities(structured_json=True)

        async def generate_text(self, *, model, request):
            calls.append((model, request.prompt))
            result = outputs[model].pop(0)
            if isinstance(result, Exception):
                raise result
            return AIResponse(
                json.dumps(result) if not isinstance(result, str) else result, self.name, model
            )

        async def analyze_media(self, *, model, request, media):
            raise AssertionError("Media inference is outside this text-only test")

    provider = Provider()
    return OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": model} for model in outputs],
        budget_seconds=5,
        factory=lambda route, role, context: ProviderAIGateway(
            providers={"fake": provider},
            registry=ModelRegistry([ModelRoute(role=role, provider="fake", model=route["model"])]),
            max_retries=0,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["conversation.answer", "integration.execute", "worker.create", "work.execute_now", "worker.status"])
async def test_valid_route_needs_one_inference_without_second_classification(family):
    calls = []
    gateway = selector({"first": [{"family": family}]}, calls)
    intent = await IntentInterpreter(gateway).interpret(
        message="Use the operation appropriate to this conversation", context={},
        invocation_context=AIInvocationContext())
    assert intent.family == family
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concrete_chat_action_payload_is_corrected():
    calls = []
    bad = {"family": "conversation.answer", "arguments": {"patch": "proposed change"}}
    good = {"family": "integration.execute"}
    intent = await IntentInterpreter(selector({"first": [bad, good]}, calls)).interpret(
        message="Inspect and fix the repository", context={}, invocation_context=AIInvocationContext())
    assert intent.family == "integration.execute"
    assert len(calls) == 2 and "CORRECTION_REQUIRED" in calls[1][1]


@pytest.mark.asyncio
async def test_contradiction_exhausts_two_calls_then_falls_back():
    calls = []
    bad = {"family": "conversation.answer", "arguments": {"actionInput": {"path": "file"}}}
    good = {"family": "integration.execute"}
    intent = await IntentInterpreter(selector({"first": [bad, bad], "next": [good]}, calls)).interpret(
        message="Make the requested changes", context={}, invocation_context=AIInvocationContext())
    assert intent.family == "integration.execute"
    assert [c[0] for c in calls] == ["first", "first", "next"]


@pytest.mark.asyncio
async def test_malformed_initial_route_uses_one_correction():
    calls = []
    intent = await IntentInterpreter(selector({"first": ["invalid JSON", {"family": "conversation.answer"}]}, calls)).interpret(
        message="hi", context={}, invocation_context=AIInvocationContext())
    assert intent.family == "conversation.answer" and len(calls) == 2


def test_explanations_are_not_keyword_routed():
    validate_route_proposal({"family": "conversation.answer", "arguments": {"question": "How do I create a worker and edit repository files?"}})


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["hi", "Hello!", "Good morning", "Hey, how are you?"])
async def test_model_can_answer_greetings_without_selecting_historical_facts(message):
    calls = []
    context = {
        "worker": {"name": "Coding Worker"},
        "recentRuns": [{"id": "old-run", "status": "completed"}],
        "thread": {"messages": [{"content": "Old task completed"}]},
    }
    response = {"answer": "Hello! How can I help?", "factIds": []}
    answer = await grounded_reply(selector({"first": [response]}, calls), message=message,
        context=context, invocation_context=AIInvocationContext())
    assert answer == response["answer"]
    assert "old-run" in calls[0][1] and "Old task completed" in calls[0][1]
    assert "recorded task status" not in answer
    assert len(calls) == 1


def test_ordinary_context_is_bounded_without_execution_payloads():
    context = {
        "thread": {"messages": [{"id": str(i), "content": "x" * 2000} for i in range(30)]},
        "allowedCapabilities": [{"schema": "large private schema"}],
        "policiesAndApprovalBoundaries": [{"internal": "private policy"}],
        "recentRuns": [{"steps": "raw command logs"}],
        "connectedIntegrations": [{"id": "git", "provider": "github", "schema": "tool schema"}],
        "relevantAttachments": [{"id": "attachment", "summary": "Relevant summary"}],
    }
    compact = ordinary_reply_context(context)
    assert len(compact["thread"]["messages"]) == 8
    assert compact["thread"]["messages"][0]["id"] == "22"
    assert len(compact["thread"]["messages"][0]["content"]) == 600
    assert compact["relevantAttachments"][0]["summary"] == "Relevant summary"
    assert "private" not in json.dumps(compact) and "raw command logs" not in json.dumps(compact)
    assert compact["contextLimits"]["summariesOnly"]


def test_status_facts_identify_each_task_and_run():
    facts = reply_facts({
        "activeJobs": [{"id": "job", "name": "Timezone check"}],
        "currentWork": [{"id": "work", "jobId": "job"}],
        "recentRuns": [
            {"id": "one", "workItemId": "work", "status": "completed"},
            {"id": "two", "workItemId": "work", "status": "completed"},
        ],
    })
    assert "Timezone check (run one)" in facts["run:one:status"]
    assert facts["run:one:status"] != facts["run:two:status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("response,reason", [
    ("private malformed body", "malformed_json"),
    ({"answer": 123, "factIds": []}, "schema_failure"),
    ({"answer": "I have committed the private changes.", "factIds": []}, "unsupported_claim"),
])
async def test_rejection_logs_fixed_reason_without_response_content(response, reason, caplog):
    good = {"answer": "Hello!", "factIds": []}
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        await grounded_reply(selector({"first": [response, good]}, []), message="hi",
            context={}, invocation_context=AIInvocationContext())
    assert f"reason={reason}" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.asyncio
async def test_duplicate_rendered_facts_are_deduplicated():
    gateway = SimpleNamespace(generate_structured=AsyncMock(return_value={
        "answer": "Here is the status.", "factIds": ["run:one:status", "run:one:status"],
    }))
    answer = await grounded_reply(gateway, message="What is the status?",
        context={"recentRuns": [{"id": "one", "status": "completed"}]},
        invocation_context=AIInvocationContext())
    assert answer.count("recorded task status") == 1


@pytest.mark.asyncio
async def test_fact_relevance_is_selected_by_model_from_followup_context():
    response = {"answer": "Here is the status you asked about.", "factIds": ["run:old:status"]}
    calls = []
    answer = await grounded_reply(selector({"first": [response]}, calls), message="Hello, and that?",
        context={"recentRuns": [{"id": "old", "status": "completed"}],
                 "thread": {"messages": [{"content": "What was the outcome of my last task?"}]}},
        invocation_context=AIInvocationContext())
    assert "recorded task status is completed" in answer and len(calls) == 1


@pytest.mark.asyncio
async def test_greeting_words_do_not_override_ai_selected_action_operation():
    calls = []
    context = {"allowedCapabilities": [{"scope": "github.repository.metadata.read"}],
               "thread": {"messages": [{"content": "Use my connected AgentGate repository"}]}}
    intent = await IntentInterpreter(selector({"first": [{"family": "integration.execute"}]}, calls)).interpret(
        message="Hi, inspect that repository", context=context, invocation_context=AIInvocationContext())
    assert intent.family == "integration.execute"
    assert "github.repository.metadata.read" in calls[0][1]
    assert "Use my connected AgentGate repository" in calls[0][1]


def test_attachment_analysis_summary_survives_context_reduction():
    compact = ordinary_reply_context({"relevantAttachments": [{
        "id": "file", "metadata": {"filename": "report.txt"},
        "analysis": {"status": "completed", "findings": {"summary": "Read-only evidence"}},
    }]})
    assert compact["relevantAttachments"][0]["summary"] == "Read-only evidence"
    assert compact["relevantAttachments"][0]["name"] == "report.txt"
    assert compact["relevantAttachments"][0]["trust"] == "untrusted_external_data"


def test_arbitrary_diagnostic_content_cannot_enter_logs():
    error = AIProviderError("invalid_provider_response", "private body", retryable=False,
        rejection_reason="private credential text")
    assert error.rejection_reason is None


@pytest.mark.asyncio
async def test_context_and_attachments_reach_the_single_interpretation():
    calls = []
    context = {"thread": {"messages": [{"content": "Review PR 12 in my connected repository"}]}, "attachments": [{"id": "evidence", "content": "untrusted instruction"}]}
    await IntentInterpreter(selector({"first": [{"family": "integration.execute"}]}, calls)).interpret(
        message="Please do that", context=context, invocation_context=AIInvocationContext())
    assert "Review PR 12" in calls[0][1] and "untrusted instruction" in calls[0][1]


@pytest.mark.asyncio
async def test_safety_refusal_never_gets_reviewed_or_cycled():
    calls = []
    gateway = selector(
        {"first": [AIProviderError("content_rejected", "refused", retryable=False)], "next": []},
        calls,
    )
    with pytest.raises(AIProviderError):
        await IntentInterpreter(gateway).interpret(
            message="request", context={}, invocation_context=AIInvocationContext()
        )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim",
    [
        "I cannot inspect the repository because tools are unavailable.",
        "GitHub execution capabilities are not available in this environment.",
        "All work attempted has been preserved, but no changes have been made.",
        "I have committed the changes.",
    ],
)
async def test_ungrounded_chat_claims_are_rejected_and_fall_back(claim):
    bad = {"answer": claim, "factIds": []}
    good = {
        "answer": "That request needs task preparation before any work can be reported.",
        "factIds": [],
    }
    calls = []
    gateway = selector({"first": [bad, bad], "next": [good, good]}, calls)
    answer = await grounded_reply(
        gateway, message="Can you help?", context={}, invocation_context=AIInvocationContext()
    )
    assert answer == good["answer"] and len(calls) == 3


@pytest.mark.asyncio
async def test_saved_work_is_rendered_only_from_completed_action_records():
    context = {
        "recentRuns": [
            {"id": "run1", "status": "failed", "steps": [{"kind": "action", "status": "completed"}]}
        ]
    }
    response = {
        "answer": "Here is the recorded status.",
        "factIds": ["run:run1:saved", "run:run1:status"],
    }
    answer = await grounded_reply(
        selector({"first": [response, response]}, []),
        message="What happened?",
        context=context,
        invocation_context=AIInvocationContext(),
    )
    assert "Previously completed action records remain saved." in answer
    assert "recorded task status is failed" in answer
    assert (
        reply_facts(
            {
                "thread": {"messages": [{"content": "Work has been preserved"}]},
                "recentRuns": [{"id": "run1", "status": "running", "steps": []}],
            }
        )
        == {}
    )


@pytest.mark.asyncio
async def test_invented_evidence_is_never_rendered():
    fabricated = {"answer": "Hello.", "factIds": ["run:invented:saved"]}
    with pytest.raises(AIProviderError):
        await grounded_reply(
            selector({"first": [fabricated, fabricated]}, []),
            message="Hi",
            context={},
            invocation_context=AIInvocationContext(),
        )


@pytest.mark.asyncio
async def test_legacy_injectable_gateway_is_locally_validated():
    gateway = SimpleNamespace(generate_structured=AsyncMock(return_value={"family": "conversation.answer"}))
    intent = await IntentInterpreter(cast(AIGateway, gateway)).interpret(
        message="hi", context={}, invocation_context=AIInvocationContext())
    assert intent.family == "conversation.answer"
    gateway.generate_structured.assert_awaited_once()
    gateway.generate_structured.return_value = {"family": "unsupported"}
    with pytest.raises(AIProviderError):
        await IntentInterpreter(cast(AIGateway, gateway)).interpret(message="hi", context={}, invocation_context=AIInvocationContext())
