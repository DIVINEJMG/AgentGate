import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.bootstrap.settings import settings
from app.domain.ai.providers import (
    AIInvocationRecorder,
    AIModelProvider,
    AIProviderError,
    AIResponse,
    AITextRequest,
)
from app.domain.ai.registry import ModelRegistry, ModelRoute
from app.infrastructure.ai.model_profiles import PlannerModelProfile
from app.infrastructure.ai.openrouter import MODEL_FORMATS, OpenRouterPlannerProvider
from app.infrastructure.database.models import PlannerAttempt, PlannerDecision
from app.runtime import smart_planner as module
from app.runtime.planner.adaptive import AdaptivePlanDecision


class Session:
    def __init__(self):
        self.decision = None
        self.attempts = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.refresh = AsyncMock()

    async def scalar(self, query):
        return self.decision

    async def scalars(self, query):
        assert self.decision is not None
        decision = self.decision
        return SimpleNamespace(
            all=lambda: [a for a in self.attempts if a.recovery_round == decision.recovery_round]
        )

    def add(self, row):
        row.id = uuid4()
        if isinstance(row, PlannerDecision):
            self.decision = row
        else:
            self.attempts.append(row)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_complex_routes", ["nvidia_nim:first", "openrouter:second"])
    monkeypatch.setattr(settings, "smart_planner_model_profiles", {
        "nvidia_nim:first": PlannerModelProfile(context_tokens=100000),
        "openrouter:second": PlannerModelProfile(context_tokens=100000),
    })
    coordinator = SimpleNamespace(
        acquire_slot=AsyncMock(return_value=object()),
        acquire_lock=AsyncMock(return_value=object()),
        release_slot=AsyncMock(),
        release_lock=AsyncMock(),
        cache_get=AsyncMock(return_value=None),
        cache_set=AsyncMock(),
        cache_delete=AsyncMock(),
        reserve_limit=AsyncMock(return_value=True),
        reserve_units=AsyncMock(return_value=True),
        reserve_ai_budget=AsyncMock(return_value=-1),
        settle_ai_budget=AsyncMock(return_value=True),
        increment_counter=AsyncMock(return_value=1),
        close=AsyncMock(),
    )
    monkeypatch.setattr(module.RedisCoordinator, "from_settings", lambda: coordinator)
    monkeypatch.setattr(module, "notify_integration_work", AsyncMock())
    session = Session()
    item = SimpleNamespace(id=uuid4(), organization_id=uuid4(), status="running", payload={})
    run = SimpleNamespace(id=uuid4(), status="running")
    return session, item, run, coordinator


def proposal():
    return AdaptivePlanDecision("finish", "Verified", "Done", "Done", "", "", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status", [408, 504])
async def test_upstream_timeout_switches_before_saved_deadline(setup, http_status):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    assert planner.decision is not None
    deadline = planner.decision.deadline
    calls = []

    async def choose(gateway):
        model = gateway._registry.route("planner").model
        calls.append(model)
        if model == "first":
            raise AIProviderError("timeout", "Upstream request failed", retryable=True, status_code=http_status)
        return proposal()

    await planner.choose({}, choose)
    assert calls == ["first", "second"]
    assert planner.decision is not None
    assert planner.decision.deadline == deadline


@pytest.mark.asyncio
async def test_ordered_fallback_and_accepted_reuse(setup):
    session, item, run, coordinator = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    calls = []

    async def choose(gateway):
        model = gateway._registry.route("planner").model
        calls.append(model)
        if model == "first":
            raise AIProviderError("provider_unavailable", "outage", retryable=True)
        return proposal()

    result = await planner.choose({"revision": "one"}, choose)
    assert result.summary == "Verified"
    assert calls == ["first", "second"]
    assert session.decision.accepted_model == "second"
    await planner.choose({"revision": "one"}, choose)
    assert calls == ["first", "second"]
    assert coordinator.release_slot.await_count == 2


@pytest.mark.asyncio
async def test_restart_keeps_route_snapshot_and_skips_interrupted_attempt(setup, monkeypatch):
    session, item, run, _ = setup
    first = module.SmartPlanner(session, item, run, 0)
    await first.prepare()
    session.attempts.append(
        PlannerAttempt(
            decision_id=session.decision.id,
            route_index=0,
            recovery_round=0,
            provider="nvidia_nim",
            model="first",
            status="started",
        )
    )
    monkeypatch.setattr(settings, "ai_complex_routes", ["nvidia_nim:changed"])
    restarted = module.SmartPlanner(session, item, run, 0)
    await restarted.prepare()
    seen = []

    async def choose(gateway):
        seen.append(gateway._registry.route("planner").model)
        return proposal()

    await restarted.choose({}, choose)
    assert seen == ["second"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category", ["content_rejected", "authentication_failed", "invalid_provider_response"]
)
async def test_failure_classification(setup, category):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    calls = []

    async def choose(gateway):
        calls.append(gateway._registry.route("planner").model)
        raise AIProviderError(
            category,
            "safe failure",
            retryable=False,
            account_scoped=category == "authentication_failed",
        )

    with pytest.raises(AIProviderError):
        await planner.choose({}, choose)
    assert calls == (["first"] if category == "content_rejected" else ["first", "second"])
    assert session.decision.status == "exhausted"


@pytest.mark.asyncio
async def test_transient_recovery_preserves_deadline_and_waits(setup):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    deadline = session.decision.deadline

    async def choose(gateway):
        raise AIProviderError("provider_unavailable", "upstream failure", retryable=True)

    with pytest.raises(AIProviderError) as error:
        await planner.choose({}, choose)
    assert error.value.retryable
    assert session.decision.recovery_round == 1
    assert session.decision.deadline == deadline
    with pytest.raises(AIProviderError) as waiting:
        await planner.choose({}, choose)
    assert waiting.value.category == "rate_limited"
    assert len(session.attempts) == 2


@pytest.mark.asyncio
async def test_expired_decision_never_calls_model(setup):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    session.decision.deadline = datetime.now(UTC) - timedelta(seconds=1)
    callback = AsyncMock()
    with pytest.raises(AIProviderError):
        await planner.choose({}, callback)
    callback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("late", [False, True])
async def test_cancellation_or_stale_generation_rejects_response(setup, late):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()

    async def choose(gateway):
        if late:
            session.decision.generation += 1
        else:
            item.status = "cancelled"
        return proposal()

    with pytest.raises(AIProviderError):
        await planner.choose({}, choose)
    assert session.decision.accepted_proposal is None


@pytest.mark.asyncio
async def test_correction_budget_two_calls_and_durable_before_dispatch(setup):
    session, item, _, coordinator = setup
    attempt = SimpleNamespace(call_count=0, transport_success=False)
    provider = SimpleNamespace(
        name="fake",
        capabilities=None,
        generate_text=AsyncMock(return_value=AIResponse("{}", "fake", "model")),
    )
    counted = module.CountedProvider(provider, session, attempt, coordinator, item.organization_id)
    for _ in range(2):
        await counted.generate_text(model="model", request=AITextRequest("system", "prompt"))
    with pytest.raises(AIProviderError):
        await counted.generate_text(model="model", request=AITextRequest("system", "prompt"))
    assert provider.generate_text.await_count == 2
    assert session.commit.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("model", list(MODEL_FORMATS))
async def test_openrouter_parameter_compatibility(model):
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": model,
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            },
        )

    provider = OpenRouterPlannerProvider(
        api_key="test-only",
        allowed_providers=["test-endpoint"],
        data_allowed=True,
        transport=httpx.MockTransport(handle),
    )
    response = await provider.generate_text(
        model=model, request=AITextRequest("system", "prompt", response_format="json_object")
    )
    assert response.model == model
    assert ("response_format" in captured[0]) == bool(MODEL_FORMATS[model])
    assert "models" not in captured[0]
    assert captured[0]["provider"]["data_collection"] == "deny"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,metadata,category,account",
    [
        (429, {}, "rate_limited", True),
        (429, {"provider_code": 429}, "rate_limited", False),
        (401, {}, "authentication_failed", True),
        (402, {}, "quota_exhausted", True),
        (503, {}, "provider_unavailable", False),
    ],
)
async def test_openrouter_errors(status, metadata, category, account):
    provider = OpenRouterPlannerProvider(
        api_key="test-only",
        allowed_providers=["test"],
        data_allowed=True,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status, headers={"Retry-After": "60"}, json={"error": {"metadata": metadata}}
            )
        ),
    )
    with pytest.raises(AIProviderError) as error:
        await provider.generate_text(model=next(iter(MODEL_FORMATS)), request=AITextRequest("", ""))
    assert error.value.category == category
    assert error.value.account_scoped == account
    assert error.value.retry_after_seconds == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["length", "error", "content_filter"])
async def test_incomplete_response_rejected(finish):
    model = next(iter(MODEL_FORMATS))
    provider = OpenRouterPlannerProvider(
        api_key="test-only",
        allowed_providers=["test"],
        data_allowed=True,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "model": model,
                    "choices": [{"finish_reason": finish, "message": {"content": "{}"}}],
                },
            )
        ),
    )
    with pytest.raises(AIProviderError):
        await provider.generate_text(model=model, request=AITextRequest("", ""))


def test_registry_keeps_order_and_separate_vision():
    registry = ModelRegistry(
        [
            ModelRoute("planner", "a", "first"),
            ModelRoute("planner", "b", "second"),
            ModelRoute("vision", "a", "vision"),
        ]
    )
    assert [route.model for route in registry.routes("planner")] == ["first", "second"]
    assert registry.route("vision").model == "vision"


@pytest.mark.asyncio
async def test_new_decision_reuses_models_and_does_not_undo_completed_work(setup):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    first = session.decision.id
    await planner.choose({}, AsyncMock(return_value=proposal()))
    session.decision = None
    session.attempts = []
    next_planner = module.SmartPlanner(session, item, run, 1)
    await next_planner.prepare()
    assert session.decision is not None
    assert session.decision.id != first
    assert session.decision.routes[0]["model"] == "first"
    completed = SimpleNamespace(id=uuid4(), status="completed", output={}, input={})
    context = module.recovery_context([completed], [], session.decision)
    assert context["mode"] == "continue"
    assert context["completedSteps"] == [str(completed.id)]
    assert "immutable" in context["rollbackBoundary"]


@pytest.mark.asyncio
async def test_all_recovery_rounds_are_bounded(setup):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()

    async def choose(gateway):
        raise AIProviderError("provider_unavailable", "outage", retryable=True)

    for round_index in range(3):
        session.decision.retry_at = None
        with pytest.raises(AIProviderError) as error:
            await planner.choose({}, choose)
        assert error.value.retryable == (round_index < 2)
    assert session.decision.status == "exhausted"
    assert len(session.attempts) == 6


@pytest.mark.asyncio
async def test_readiness_cache_and_context_rejection():
    calls = []
    model = next(iter(MODEL_FORMATS))

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": model,
                        "supported_parameters": ["response_format"],
                        "context_length": 262144,
                    }
                ]
            },
        )

    provider = OpenRouterPlannerProvider(
        api_key="test-only",
        allowed_providers=["test"],
        data_allowed=True,
        transport=httpx.MockTransport(handle),
    )
    cache = {}

    async def get(key):
        return cache.get(key)

    async def put(key, value, **kwargs):
        cache[key] = value

    coordinator = SimpleNamespace(cache_get=get, cache_set=put)
    assert (await provider.readiness(coordinator))[model]["compatible"]
    await provider.readiness(coordinator)
    assert len(calls) == 1
    with pytest.raises(AIProviderError) as error:
        await provider.generate_text(model=model, request=AITextRequest("", "a" * 256001))
    assert error.value.category == "context_too_large"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_data_handling_and_paid_models_fail_closed():
    handler = AsyncMock()
    provider = OpenRouterPlannerProvider(
        api_key="test-only",
        allowed_providers=[],
        data_allowed=False,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AIProviderError):
        await provider.generate_text(model=next(iter(MODEL_FORMATS)), request=AITextRequest("", ""))
    handler.assert_not_called()
    provider.data_allowed, provider.allowed_providers = True, ["test"]
    with pytest.raises(AIProviderError):
        await provider.generate_text(model="paid/model", request=AITextRequest("", ""))
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_json_repairs_share_two_call_budget_then_switch(setup, monkeypatch):
    from app.domain.ai.providers import ModelProviderCapabilities

    session, item, run, _ = setup
    monkeypatch.setattr(module, "DurableAIInvocationRecorder", lambda: None)
    calls = []

    class Provider:
        capabilities = ModelProviderCapabilities(structured_json=True)

        def __init__(self, name):
            self.name = name

        async def generate_text(self, *, model, request):
            calls.append(model)
            return AIResponse(
                "not JSON" if model == "first" else '{"answer": "ok"}', self.name, model
            )

    monkeypatch.setattr(module, "provider_for", lambda route: Provider(route["provider"]))
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()

    async def choose(gateway):
        result = await gateway.generate_structured(
            role="planner",
            system="system",
            prompt="task",
            schema_name="test",
            schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        )
        assert result["answer"] == "ok"
        return proposal()

    await planner.choose({}, choose)
    assert calls == ["first", "first", "second"]
    assert session.attempts[0].call_count == 2
    assert session.attempts[0].transport_success
    assert not session.attempts[0].schema_valid
    assert session.attempts[1].schema_valid and session.attempts[1].accepted


@pytest.mark.asyncio
async def test_native_schema_and_unexpected_model():
    model = "nvidia/nemotron-3-super-120b-a12b:free"
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json={"model": "unrequested/model", "choices": [{"message": {"content": "{}"}}]}
        )

    provider = OpenRouterPlannerProvider(
        api_key="test-only",
        allowed_providers=["test"],
        data_allowed=True,
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(AIProviderError):
        await provider.generate_text(
            model=model,
            request=AITextRequest(
                "",
                "",
                response_format="json_object",
                json_schema={"type": "object"},
                schema_name="decision",
            ),
        )
    assert bodies[0]["response_format"]["type"] == "json_schema"
    assert bodies[0]["provider"]["require_parameters"] is True


@pytest.mark.asyncio
async def test_transport_circuit_and_one_recovery_probe(setup):
    session, item, run, coordinator = setup
    coordinator.increment_counter.return_value = 5
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    async def choose(gateway):
        if gateway._registry.route("planner").model == "first":
            raise AIProviderError("provider_unavailable", "outage", retryable=True)
        return proposal()
    await planner.choose({}, choose)
    assert any(call.args[1] == "cooldown" for call in coordinator.cache_set.await_args_list)
    coordinator.acquire_lock.assert_not_awaited()
    session.decision = None
    session.attempts = []
    async def cache(key):
        return "5" if key.endswith(":failures") else None
    coordinator.cache_get.side_effect = cache
    recovered = module.SmartPlanner(session, item, run, 1)
    await recovered.prepare()
    await recovered.choose({}, AsyncMock(return_value=proposal()))
    coordinator.acquire_lock.assert_awaited_once()
    coordinator.release_lock.assert_awaited_once()


@pytest.mark.asyncio
async def test_all_twelve_routes_share_one_ordered_decision(setup, monkeypatch):
    from app.bootstrap.settings import Settings
    config = Settings.model_construct()
    monkeypatch.setattr(settings, "ai_complex_routes", config.smart_planner_routes)
    monkeypatch.setattr(settings, "smart_planner_model_profiles", {})
    monkeypatch.setattr(settings, "ai_coordinator_model", config.ai_coordinator_model)
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    seen = []
    async def choose(gateway):
        seen.append(gateway._registry.route("planner").model)
        if len(seen) < 12:
            raise AIProviderError("invalid_provider_response", "bad decision", retryable=False)
        return proposal()
    await planner.choose({}, choose)
    assert seen == [route["model"] for route in session.decision.routes]
    assert len(session.attempts) == 12 and sum(a.accepted for a in session.attempts) == 1


@pytest.mark.asyncio
async def test_shared_account_failure_skips_same_account_models(setup, monkeypatch):
    monkeypatch.setattr(settings, "ai_complex_routes", ["nvidia_nim:first", "nvidia_nim:another", "openrouter:second"])
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    seen = []
    async def choose(gateway):
        model = gateway._registry.route("planner").model
        seen.append(model)
        if model == "first":
            raise AIProviderError("rate_limited", "account limit", retryable=True, account_scoped=True, retry_after_seconds=60)
        return proposal()
    await planner.choose({}, choose)
    assert seen == ["first", "second"]


@pytest.mark.asyncio
async def test_deadline_reports_untried_instead_of_claiming_all_failed(setup):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    async def choose(gateway):
        session.attempts[-1].call_count = 1
        session.decision.deadline = datetime.now(UTC) - timedelta(seconds=1)
        raise AIProviderError("timeout", "timeout", retryable=True)
    with pytest.raises(AIProviderError) as error:
        await planner.choose({}, choose)
    assert "untried" in str(error.value)
    assert item.payload["runtime"]["plannerExhaustion"] == {
        "reason": "deadline", "failed": 1, "untried": 1, "unavailable": 0}


@pytest.mark.asyncio
async def test_model_failure_logs_identity_and_reason_without_provider_body(setup, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    async def choose(gateway):
        if gateway._registry.route("planner").model == "first":
            raise AIProviderError("rate_limited", "DO_NOT_LOG_THIS_SECRET_BODY", retryable=True, status_code=429, retry_after_seconds=30)
        return proposal()
    await planner.choose({}, choose)
    assert "model=first" in caplog.text and "category=rate_limited" in caplog.text
    assert "http_status=429" in caplog.text and "retry_after_seconds=30" in caplog.text
    assert "Planner model accepted" in caplog.text and "model=second" in caplog.text
    assert "DO_NOT_LOG_THIS_SECRET_BODY" not in caplog.text


@pytest.mark.asyncio
async def test_timeout_pauses_without_switching_models(setup):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    callback = AsyncMock(side_effect=AIProviderError("timeout", "transport wait", retryable=True))
    with pytest.raises(AIProviderError) as error:
        await planner.choose({}, callback)
    assert not error.value.retryable
    assert callback.await_count == 1
    assert session.decision.status == "exhausted"
    assert session.decision.recovery_round == 0


@pytest.mark.asyncio
async def test_attempt_uses_remaining_decision_budget_not_old_cutoff(setup, monkeypatch):
    session, item, run, _ = setup
    monkeypatch.setattr(settings, "smart_planner_attempt_seconds", 1)
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    original_timeout = asyncio.timeout
    limits = []
    def capture(seconds):
        limits.append(seconds)
        return original_timeout(seconds)
    monkeypatch.setattr(module.asyncio, "timeout", capture)
    await planner.choose({}, AsyncMock(return_value=proposal()))
    assert limits and limits[0] > 1000


@pytest.mark.asyncio
async def test_org_quota_stops_selection_despite_previous_transient_error(setup, caplog):
    session, item, run, _ = setup
    planner = module.SmartPlanner(session, item, run, 0)
    await planner.prepare()
    async def choose(gateway):
        if gateway._registry.route("planner").model == "first":
            session.attempts[-1].call_count = 1
            raise AIProviderError("provider_unavailable", "outage", retryable=True)
        raise AIProviderError("quota_exhausted", "local budget", retryable=False, organization_scoped=True)
    with pytest.raises(AIProviderError) as error:
        await planner.choose({}, choose)
    assert not error.value.retryable and error.value.organization_scoped
    assert session.decision.status == "exhausted"
    assert session.decision.recovery_round == 0 and session.decision.retry_at is None
    assert session.attempts[-1].status == "blocked"
    assert "Planner not called" in caplog.text


@pytest.mark.asyncio
async def test_bad_host_configuration_is_rejected_before_budget_reservation(setup):
    session, item, _, coordinator = setup
    provider = OpenRouterPlannerProvider(api_key="test", allowed_providers=["google/gemma-4-31b-it:free"], data_allowed=True)
    attempt = SimpleNamespace(call_count=0)
    counted = module.CountedProvider(provider, session, attempt, coordinator, item.organization_id)
    with pytest.raises(AIProviderError) as error:
        await counted.generate_text(model="google/gemma-4-31b-it:free", request=AITextRequest("", ""))
    assert error.value.category == "configuration_missing"
    coordinator.reserve_units.assert_not_awaited()
    coordinator.reserve_limit.assert_not_awaited()
    assert attempt.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("daily", [False, True])
async def test_local_admission_is_organization_scoped_and_never_calls_model(setup, daily):
    session, item, _, coordinator = setup
    if daily:
        coordinator.reserve_ai_budget.return_value = 1
    else:
        coordinator.reserve_ai_budget.return_value = 0
    provider = SimpleNamespace(name="fake", capabilities=None, generate_text=AsyncMock())
    attempt = SimpleNamespace(call_count=0)
    counted = module.CountedProvider(provider, session, attempt, coordinator, item.organization_id)
    with pytest.raises(AIProviderError) as error:
        await counted.generate_text(model="model", request=AITextRequest("", ""))
    assert error.value.organization_scoped and not error.value.account_scoped
    assert error.value.retryable is (not daily)
    provider.generate_text.assert_not_awaited()
    assert attempt.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("expired,category", [(True, "timeout"), (False, "cancelled")])
async def test_cancelled_inference_records_timing_without_swallowing_cancellation(expired, category):
    from app.application.services.ai_gateway import ProviderAIGateway
    from app.domain.ai.providers import ModelProviderCapabilities
    recorder = SimpleNamespace(start=AsyncMock(return_value=uuid4()), finish=AsyncMock())
    provider = SimpleNamespace(name="test", capabilities=ModelProviderCapabilities(), generate_text=AsyncMock(side_effect=asyncio.CancelledError))
    gateway = ProviderAIGateway(
        providers=cast(dict[str, AIModelProvider], {"test": provider}),
        registry=ModelRegistry([ModelRoute(role="planner", provider="test", model="test")]),
        recorder=cast(AIInvocationRecorder, recorder),
        cancellation_deadline=datetime.now(UTC) + timedelta(seconds=-1 if expired else 100),
    )
    with pytest.raises(asyncio.CancelledError):
        await gateway.generate_text(role="planner", system="", prompt="")
    result = recorder.finish.await_args.kwargs
    assert result["error_category"] == category
    assert result["latency_ms"] >= 0 and result["success"] is False
