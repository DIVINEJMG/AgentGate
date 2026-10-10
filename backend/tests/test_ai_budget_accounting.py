import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.services.conversations import ConversationService
from app.application.services.ordered_ai import OrderedTextGateway
from app.bootstrap.settings import Settings
from app.domain.ai.providers import AIInvocationContext, AIProviderError, AIResponse, AITextRequest
from app.infrastructure.ai.model_profiles import PlannerModelProfile
from app.infrastructure.ai.text_routes import AdmittedTextProvider
from app.infrastructure.ai.workloads import reserve_workload_budgets, settle_workload_budget


def coordinator():
    return SimpleNamespace(
        cache_get=AsyncMock(return_value=None), acquire_slot=AsyncMock(return_value="slot"),
        release_slot=AsyncMock(), reserve_ai_budget=AsyncMock(return_value=-1),
        settle_ai_budget=AsyncMock(return_value=True), cache_delete=AsyncMock(),
        cache_set=AsyncMock(), increment_counter=AsyncMock(return_value=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("called,usage,charge", [
    (False, None, 0), (True, {"total_tokens": 30}, 30),
    (True, {"prompt_tokens": 10, "completion_tokens": 15}, 25),
    (True, {}, 100), (True, {"total_tokens": -1}, 100),
    (True, {"total_tokens": 150}, 150),
])
async def test_settlement_uses_reported_usage_or_honest_unknown_bound(called, usage, charge):
    redis = coordinator()
    reservation = await reserve_workload_budgets(redis, Settings.model_construct(), "org", "complex", 100)
    await settle_workload_budget(redis, reservation, called=called, usage=usage)
    assert redis.reserve_ai_budget.await_count == 1
    args = redis.settle_ai_budget.await_args
    assert args.args[0] == reservation.identity
    assert len(args.args[1]) == 4
    assert args.kwargs == {"actual_tokens": charge, "called": called}


@pytest.mark.asyncio
async def test_atomic_rejection_has_scope_no_call_and_no_refund(caplog):
    redis = coordinator()
    redis.reserve_ai_budget.return_value = 3
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"), pytest.raises(AIProviderError) as error:
        await reserve_workload_budgets(redis, Settings.model_construct(), "org", "complex", 100)
    assert error.value.budget_scope == "organization_tokens"
    assert error.value.organization_scoped and error.value.model_called is False
    redis.settle_ai_budget.assert_not_awaited()
    assert "model_called=false budget=organization_tokens" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [False, True])
async def test_admission_refunds_unused_reservation_or_reconciles_usage(monkeypatch, blocked):
    redis = coordinator()
    if blocked:
        redis.acquire_slot.side_effect = ["org-slot", None]
    monkeypatch.setattr("app.infrastructure.ai.text_routes.RedisCoordinator.from_settings", lambda: redis)
    provider = SimpleNamespace(name="fake", capabilities=None, validate_request=lambda **kwargs: None,
        generate_text=AsyncMock(return_value=AIResponse("ok", "fake", "model", usage={"total_tokens": 30})))
    admitted = AdmittedTextProvider(provider, Settings.model_construct(),
        AIInvocationContext(organization_id=uuid4()), "fake-key", PlannerModelProfile(context_tokens=10000))
    if blocked:
        with pytest.raises(AIProviderError):
            await admitted.generate_text(model="model", request=AITextRequest("rules", "hi"))
        provider.generate_text.assert_not_awaited()
    else:
        await admitted.generate_text(model="model", request=AITextRequest("rules", "hi"))
        provider.generate_text.assert_awaited_once()
    assert redis.settle_ai_budget.await_args.kwargs == {
        "actual_tokens": 0 if blocked else 30, "called": not blocked,
    }
    redis.release_slot.assert_awaited()


@pytest.mark.asyncio
async def test_cancelled_dispatch_retains_unknown_usage_and_propagates_cancellation(monkeypatch):
    redis = coordinator()
    monkeypatch.setattr("app.infrastructure.ai.text_routes.RedisCoordinator.from_settings", lambda: redis)
    provider = SimpleNamespace(name="fake", capabilities=None, validate_request=lambda **kwargs: None,
        generate_text=AsyncMock(side_effect=asyncio.CancelledError))
    admitted = AdmittedTextProvider(provider, Settings.model_construct(),
        AIInvocationContext(organization_id=uuid4()), "fake-key", PlannerModelProfile(context_tokens=10000))
    with pytest.raises(asyncio.CancelledError):
        await admitted.generate_text(model="model", request=AITextRequest("rules", "hi"))
    assert redis.settle_ai_budget.await_args.kwargs["called"]
    assert redis.settle_ai_budget.await_args.kwargs["actual_tokens"] > 0


@pytest.mark.asyncio
async def test_provider_quota_skips_same_account_then_calls_independent_account():
    calls = []

    class Gateway:
        def __init__(self, route):
            self.route = route

        async def generate_text(self, **kwargs):
            calls.append(self.route["model"])
            if self.route["provider"] == "exhausted":
                raise AIProviderError("quota_exhausted", "provider quota", retryable=False)
            return AIResponse("hello", "independent", "next")

    gateway = OrderedTextGateway(legacy=None, budget_seconds=5,
        routes=[{"provider": "exhausted", "model": "one"},
                {"provider": "exhausted", "model": "two"},
                {"provider": "independent", "model": "next"}],
        factory=lambda route, *args: Gateway(route))
    result = await gateway.generate_text(role="conversation", system="rules", prompt="hi")
    assert result.text == "hello" and calls == ["one", "next"]


def test_local_budget_message_does_not_blame_provider_allowance():
    error = AIProviderError("quota_exhausted", "local budget", retryable=False,
        organization_scoped=True, budget_scope="organization_tokens", model_called=False)
    message = ConversationService.__new__(ConversationService)._provider_message(error)
    assert "local AI budget" in message and "no model was called for this attempt" in message
