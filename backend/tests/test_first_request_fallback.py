import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.application.services.ai_gateway import ProviderAIGateway, observe_inference
from app.application.services.ordered_ai import OrderedTextGateway
from app.bootstrap.settings import Settings
from app.domain.ai.providers import (
    AIInvocationContext,
    AIProviderError,
    AIResponse,
    ModelProviderCapabilities,
)
from app.domain.ai.registry import ModelRegistry, ModelRoute
from app.infrastructure.ai import text_routes
from app.infrastructure.ai.provider import ai_gateway_from_settings


def gateway(outputs, calls, role):
    class Provider:
        name = "fake"
        capabilities = ModelProviderCapabilities(text_input=True, structured_json=True)

        async def generate_text(self, *, model, request):
            calls.append((model, request.prompt))
            value = outputs[model].pop(0)
            if isinstance(value, Exception):
                raise value
            return AIResponse(text=value, provider=self.name, model=model)

        async def analyze_media(self, *, model, request, media):
            raise AssertionError("Media inference is outside this text-only test")

    provider = Provider()
    return lambda route, selected_role, context: ProviderAIGateway(
        providers={"fake": provider},
        registry=ModelRegistry(
            [ModelRoute(role=selected_role, provider="fake", model=route["model"])]
        ),
        max_retries=0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,method", [("intent", "generate_structured"), ("conversation", "generate_text")]
)
async def test_first_request_tries_remaining_models_before_unavailable(role, method, caplog):
    calls = []
    outputs = {
        "first": [AIProviderError("provider_unavailable", "private body", retryable=True)],
        "next": ['{"answer":"ok"}'],
    }
    ordered = OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": m} for m in outputs],
        factory=gateway(outputs, calls, role),
        budget_seconds=5,
    )
    kwargs = {
        "role": role,
        "system": "same rules",
        "prompt": "hi",
        "context": AIInvocationContext(correlation_id="initial-request"),
    }
    if method == "generate_structured":
        kwargs.update(
            schema_name="request",
            schema={
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
                "additionalProperties": False,
            },
        )
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = await getattr(ordered, method)(**kwargs)
    assert [c[0] for c in calls] == ["first", "next"]
    assert "AI selection accepted" in caplog.text
    assert "private body" not in caplog.text
    assert result


@pytest.mark.asyncio
async def test_schema_correction_exhausts_one_model_then_switches():
    calls = []
    outputs = {"first": ["broken", '{"count":"wrong type"}'], "next": ['{"count":1}']}
    ordered = OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": m} for m in outputs],
        factory=gateway(outputs, calls, "intent"),
        budget_seconds=5,
    )
    result = await ordered.generate_structured(
        role="intent",
        system="rules",
        prompt="create worker",
        schema_name="worker",
        schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    )
    assert result == {"count": 1}
    assert [c[0] for c in calls] == ["first", "first", "next"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,account,org,expected",
    [
        ("authentication_failed", True, False, ["first", "independent"]),
        ("content_rejected", False, False, ["first"]),
        ("quota_exhausted", False, True, ["first"]),
    ],
)
async def test_account_failures_skip_same_account_but_safety_and_org_stop(
    category, account, org, expected
):
    calls = []
    outputs = {
        "first": [
            AIProviderError(
                category, "stop", retryable=False, account_scoped=account, organization_scoped=org
            )
        ],
        "same": ["ok"],
        "independent": ["ok"],
    }
    routes = [
        {"provider": "one", "model": "first"},
        {"provider": "one", "model": "same"},
        {"provider": "two", "model": "independent"},
    ]
    ordered = OrderedTextGateway(
        legacy=None,
        routes=routes,
        factory=gateway(outputs, calls, "conversation"),
        budget_seconds=5,
    )
    if category == "authentication_failed":
        await ordered.generate_text(role="conversation", system="rules", prompt="hello")
    else:
        with pytest.raises(AIProviderError):
            await ordered.generate_text(role="conversation", system="rules", prompt="hello")
    assert [c[0] for c in calls] == expected


def test_settings_wire_worker_and_chat_fallback_without_changing_vision():
    config = Settings(
        ai_enabled=True, smart_planner_enabled=True, ai_coordinator_api_key=SecretStr("fake")
    )
    gateway = ai_gateway_from_settings(config)
    assert isinstance(gateway, OrderedTextGateway)
    assert len(gateway.routes) == 7
    assert gateway.routes[0]["provider"] == "nvidia_nim"
    assert gateway.routes[0]["model"] == "openai/gpt-oss-20b"
    assert gateway.budget_seconds == 1500
    assert gateway.attempt_seconds == 250
    assert gateway.legacy._registry.route("vision").model == config.ai_vision_model
    assert isinstance(
        ai_gateway_from_settings(config.model_copy(update={"smart_planner_enabled": False})),
        ProviderAIGateway,
    )


@pytest.mark.asyncio
async def test_waiting_logs_continue_without_response_data(caplog):
    async def operation():
        await asyncio.sleep(0.02)
        raise AIProviderError("provider_unavailable", "secret error", retryable=True)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"), pytest.raises(AIProviderError):
        await observe_inference(
            operation,
            role="planner",
            model="first",
            provider="fake",
            context=AIInvocationContext(),
            heartbeat_seconds=0.005,
        )
    assert "AI inference waiting" in caplog.text
    assert "AI inference failed" in caplog.text
    assert "outcome=provider_unavailable" in caplog.text
    assert "secret error" not in caplog.text


@pytest.mark.asyncio
async def test_first_request_budget_blocks_before_transport(monkeypatch):
    provider = SimpleNamespace(
        name="fake",
        capabilities=ModelProviderCapabilities(),
        validate_request=lambda **kw: None,
        generate_text=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        cache_get=AsyncMock(return_value=None),
        acquire_slot=AsyncMock(return_value="slot"),
        reserve_limit=AsyncMock(return_value=True),
        reserve_units=AsyncMock(return_value=False),
        reserve_ai_budget=AsyncMock(return_value=1),
        settle_ai_budget=AsyncMock(),
        release_slot=AsyncMock(),
    )
    monkeypatch.setattr(text_routes.RedisCoordinator, "from_settings", lambda: coordinator)
    from app.domain.ai.providers import AITextRequest
    from app.infrastructure.ai.model_profiles import PlannerModelProfile

    admitted = text_routes.AdmittedTextProvider(
        provider,
        Settings(),
        AIInvocationContext(organization_id=uuid4()),
        "key",
        PlannerModelProfile(context_tokens=10000),
    )
    with pytest.raises(AIProviderError) as error:
        await admitted.generate_text(
            model="one", request=AITextRequest(system="rules", prompt="request")
        )
    assert error.value.organization_scoped
    provider.generate_text.assert_not_awaited()
    coordinator.release_slot.assert_awaited_once()


@pytest.mark.asyncio
async def test_interactive_attempt_timeout_switches_without_using_overall_deadline(caplog):
    calls = []

    class FakeGateway:
        model: str
        async def generate_text(self, **kwargs):
            calls.append((self.model, kwargs.get("stream")))
            if self.model == "slow":
                await asyncio.sleep(1)
            return AIResponse(text="ok", provider="fake", model=self.model)

    def factory(route, role, context):
        gateway = FakeGateway()
        gateway.model = route["model"]
        return gateway

    ordered = OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": m} for m in ["slow", "next"]],
        factory=factory,
        budget_seconds=1,
        attempt_seconds=0.02,
    )
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        result = await ordered.generate_text(
            role="conversation", system="rules", prompt="hi", stream=True
        )
    assert result.model == "next"
    assert calls == [("slow", False), ("next", False)]
    assert "AI attempt timed out" in caplog.text


@pytest.mark.asyncio
async def test_interpretation_and_reply_share_one_deadline():
    clock = [0.0]
    calls = []
    outputs = {"first": ['{"answer":"ok"}', "reply"]}
    ordered = OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": "first"}],
        factory=gateway(outputs, calls, "intent"),
        budget_seconds=1500,
        clock=lambda: clock[0],
    )
    await ordered.generate_structured(
        role="intent", system="rules", prompt="hi", schema_name="request", schema={"type": "object"}
    )
    clock[0] = 1501
    with pytest.raises(AIProviderError) as error:
        await ordered.generate_text(role="conversation", system="rules", prompt="hi")
    assert error.value.category == "timeout"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rate_limited", "timeout"])
async def test_previous_invocation_ends_and_closes_before_fallback(failure):
    events = []

    class FakeGateway:
        def __init__(self, model):
            self.model = model

        async def generate_text(self, **kwargs):
            events.append((self.model, "started"))
            try:
                if self.model == "first":
                    if failure == "timeout":
                        await asyncio.Event().wait()
                    raise AIProviderError("rate_limited", "quota", retryable=True)
                assert events[:3] == [
                    ("first", "started"), ("first", "ended"), ("first", "closed")
                ]
                return AIResponse(text="ok", provider="fake", model=self.model)
            finally:
                events.append((self.model, "ended"))

        async def close(self):
            await asyncio.sleep(0)
            events.append((self.model, "closed"))

    ordered = OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": model} for model in ("first", "next")],
        factory=lambda route, *args: FakeGateway(route["model"]),
        budget_seconds=1,
        attempt_seconds=0.02,
    )
    result = await ordered.generate_text(role="conversation", system="rules", prompt="hi")
    assert result.model == "next"
    assert events == [
        ("first", "started"), ("first", "ended"), ("first", "closed"),
        ("next", "started"), ("next", "ended"), ("next", "closed"),
    ]


@pytest.mark.asyncio
async def test_cancelled_request_does_not_start_fallback():
    events = []
    started = asyncio.Event()

    class FakeGateway:
        async def generate_text(self, **kwargs):
            events.append("started")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("ended")

        async def close(self):
            events.append("closed")

    ordered = OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": model} for model in ("first", "next")],
        factory=lambda *args: FakeGateway(),
        budget_seconds=1,
    )
    task = asyncio.create_task(ordered.generate_text(
        role="conversation", system="rules", prompt="hi"
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["started", "ended", "closed"]


@pytest.mark.asyncio
async def test_correction_consumes_same_attempt_allowance():
    calls = []

    class Provider:
        name = "fake"
        capabilities = ModelProviderCapabilities(text_input=True, structured_json=True)

        async def generate_text(self, *, model, request):
            calls.append(model)
            if model == "slow":
                await asyncio.sleep(0.04)
                text = "malformed"
            else:
                text = '{"answer":"ok"}'
            return AIResponse(text=text, provider="fake", model=model)

        async def analyze_media(self, *, model, request, media):
            raise AssertionError("Media inference is outside this text-only test")

    def factory(route, role, context):
        return ProviderAIGateway(
            providers={"fake": Provider()},
            registry=ModelRegistry([ModelRoute(role=role, provider="fake", model=route["model"])]),
            max_retries=0,
        )

    ordered = OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": m} for m in ["slow", "next"]],
        factory=factory,
        budget_seconds=1,
        attempt_seconds=0.06,
    )
    result = await ordered.generate_structured(
        role="intent", system="rules", prompt="hi", schema_name="request", schema={"type": "object"}
    )
    assert result == {"answer": "ok"}
    assert calls == ["slow", "slow", "next"]


@pytest.mark.asyncio
async def test_overall_timeout_does_not_switch_to_untried_model():
    calls = []

    class Fake:
        async def generate_text(self, **kwargs):
            calls.append("first")
            await asyncio.sleep(1)

    ordered = OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": m} for m in ["first", "next"]],
        factory=lambda *args: Fake(),
        budget_seconds=0.02,
        attempt_seconds=250,
    )
    with pytest.raises(AIProviderError) as error:
        await ordered.generate_text(role="conversation", system="rules", prompt="hi")
    assert error.value.category == "timeout"
    assert calls == ["first"]


@pytest.mark.asyncio
async def test_actual_interpreter_and_worker_drafter_start_with_fallback():
    from app.application.services.intent_interpreter import IntentInterpreter
    from app.application.services.worker_draft import WorkerDraftGenerator

    calls = []
    intent = {"family": "conversation.answer", "arguments": {}}
    draft = {
        "suggested_name": "Code Worker",
        "role": "Developer",
        "charter": "Review authorized code",
        "initial_jobs": [{"name": "Review code", "objective": "Review repository changes"}],
    }
    outputs = {
        "first": [AIProviderError("provider_unavailable", "failed", retryable=True)] * 2,
        "next": [json.dumps(intent), json.dumps(draft)],
    }
    ordered = OrderedTextGateway(
        legacy=None,
        routes=[{"provider": "fake", "model": m} for m in outputs],
        factory=gateway(outputs, calls, "intent"),
        budget_seconds=5,
    )
    context = AIInvocationContext(organization_id=uuid4())
    answer = await IntentInterpreter(ordered).interpret(
        message="hi", context={}, invocation_context=context
    )
    ordered.workload_factory = lambda workload, purpose: OrderedTextGateway(
        legacy=None, routes=ordered.routes, factory=ordered.factory, budget_seconds=5,
        workload=workload, purpose=purpose)
    worker = await WorkerDraftGenerator(ordered).generate(
        instruction="Create a code reviewer", authoritative_context={}, invocation_context=context
    )
    assert answer.family == "conversation.answer"
    assert worker.suggested_name == "Code Worker"
    assert [c[0] for c in calls] == ["first", "next", "first", "next"]


@pytest.mark.asyncio
async def test_shared_admission_outage_is_not_reported_as_model_outage(monkeypatch):
    provider = SimpleNamespace(
        name="fake",
        capabilities=ModelProviderCapabilities(),
        validate_request=lambda **kw: None,
        generate_text=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        cache_get=AsyncMock(side_effect=ConnectionError("private connection data"))
    )
    monkeypatch.setattr(text_routes.RedisCoordinator, "from_settings", lambda: coordinator)
    from app.domain.ai.providers import AITextRequest
    from app.infrastructure.ai.model_profiles import PlannerModelProfile

    admitted = text_routes.AdmittedTextProvider(
        provider,
        Settings(),
        AIInvocationContext(),
        "key",
        PlannerModelProfile(context_tokens=10000),
    )
    with pytest.raises(AIProviderError) as error:
        await admitted.generate_text(
            model="one", request=AITextRequest(system="rules", prompt="hi")
        )
    assert error.value.organization_scoped
    assert error.value.category == "configuration_missing"
    assert "private connection data" not in str(error.value)
    provider.generate_text.assert_not_awaited()
