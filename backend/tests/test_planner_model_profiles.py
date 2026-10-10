import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.bootstrap.settings import Settings, settings
from app.domain.ai.providers import AIProviderError, AITextRequest
from app.infrastructure.ai.model_profiles import (
    DEFAULT_PROFILES,
    PlannerModelProfile,
    model_profile,
)
from app.infrastructure.ai.nvidia import NvidiaNimProvider
from app.infrastructure.ai.openrouter import OpenRouterPlannerProvider
from app.runtime.smart_planner import configured_routes, provider_for


def test_twelve_ordered_models_and_disabled_rollout(monkeypatch):
    config = Settings.model_construct()
    monkeypatch.setattr(settings, "ai_complex_routes", config.smart_planner_routes)
    monkeypatch.setattr(settings, "smart_planner_model_profiles", {})
    monkeypatch.setattr(settings, "ai_coordinator_model", config.ai_coordinator_model)
    routes = configured_routes()
    assert len(routes) == 12
    assert len(DEFAULT_PROFILES) == 15
    assert [route["model"] for route in routes] == [
        "moonshotai/kimi-k3",
        "z-ai/glm-5.3",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "z-ai/glm-5.3-flash",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "poolside/laguna-xs-2.1",
        "cohere/north-mini-code:free",
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-3.5-lightning:free",
        "google/gemma-4-26b-a4b-it:free",
        "openai/gpt-oss-20b",
        "thinkingmachines/inkling:free",
    ]
    assert not config.smart_planner_enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [key for key in DEFAULT_PROFILES if key.startswith(("nvidia_nim:", "openrouter:"))])
async def test_all_models_use_shared_request_and_own_compatibility(key):
    provider_name, _, model = key.partition(":")
    profile = DEFAULT_PROFILES[key]
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": model,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "{}", "reasoning_content": "private"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handle)
    provider = (
        NvidiaNimProvider(api_key="test", planner_profile=profile, transport=transport)
        if provider_name == "nvidia_nim"
        else OpenRouterPlannerProvider(
            api_key="test", data_allowed=True, allowed_providers=["test"], transport=transport
        )
    )
    result = await provider.generate_text(
        model=model,
        request=AITextRequest(
            "same authority", "same evidence", temperature=0.2, response_format="json_object"
        ),
    )
    body = captured[0]
    assert body["model"] == model and body["temperature"] == 0.2
    assert body["messages"][0]["content"] == "same authority"
    assert body["max_tokens"] >= profile.generation_tokens
    assert result.text == "{}" and "private" not in result.text
    if "kimi" in model or "glm" in model:
        assert "enable_thinking" not in body.get("chat_template_kwargs", {})


@pytest.mark.asyncio
async def test_future_model_requires_only_profile_and_route(monkeypatch):
    key = "openrouter:example/future:free"
    profile = PlannerModelProfile(context_tokens=50000, output_format="prompt_json")
    monkeypatch.setattr(settings, "smart_planner_model_profiles", {key: profile})
    monkeypatch.setattr(settings, "ai_complex_routes", [key])
    assert configured_routes()[0]["profile"] == profile.model_dump()
    provider = OpenRouterPlannerProvider(
        api_key="test",
        data_allowed=True,
        allowed_providers=["test"],
        models=["example/future:free"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"model": "example/future:free", "choices": [{"message": {"content": "{}"}}]},
            )
        ),
    )
    assert (
        await provider.generate_text(model="example/future:free", request=AITextRequest("", ""))
    ).text == "{}"


def test_profile_snapshot_survives_configuration_change(monkeypatch):
    key = "nvidia_nim:moonshotai/kimi-k3"
    monkeypatch.setattr(settings, "ai_complex_routes", [key])
    route = configured_routes()[0]
    monkeypatch.setattr(
        settings, "smart_planner_model_profiles", {key: PlannerModelProfile(context_tokens=2000)}
    )
    provider = provider_for(route)
    assert isinstance(provider, NvidiaNimProvider) and provider.planner_profile is not None
    assert provider.planner_profile.context_tokens == 1048576


def test_profile_rejects_routing_or_credential_overrides():
    with pytest.raises(ValidationError):
        PlannerModelProfile(context_tokens=5000, request_parameters={"model": "other"})
    with pytest.raises(ValidationError):
        PlannerModelProfile(context_tokens=5000, request_parameters={"api_key": "secret"})


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["length", "error", "content_filter"])
async def test_nvidia_rejects_incomplete_or_refused_json(finish):
    provider = NvidiaNimProvider(
        api_key="test",
        planner_profile=DEFAULT_PROFILES["nvidia_nim:moonshotai/kimi-k3"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"finish_reason": finish, "message": {"content": "{}"}}]}
            )
        ),
    )
    with pytest.raises(AIProviderError) as error:
        await provider.generate_text(model="moonshotai/kimi-k3", request=AITextRequest("", ""))
    assert error.value.category == (
        "content_rejected" if finish == "content_filter" else "invalid_provider_response"
    )
    assert error.value.rejection_reason == (
        "truncated_output" if finish == "length" else "incomplete_output"
    )


@pytest.mark.asyncio
async def test_nvidia_empty_output_has_sanitized_diagnostic():
    provider = NvidiaNimProvider(api_key="test", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": ""},
        }]})))
    with pytest.raises(AIProviderError) as error:
        await provider.generate_text(model="openai/gpt-oss-20b", request=AITextRequest("", "hi"))
    assert error.value.rejection_reason == "empty_output"


@pytest.mark.asyncio
async def test_nvidia_shared_rate_limit_has_account_cooldown():
    provider = NvidiaNimProvider(
        api_key="test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "45"}, text="quota")
        ),
    )
    with pytest.raises(AIProviderError) as error:
        await provider.generate_text(model="model", request=AITextRequest("", ""))
    assert error.value.account_scoped and error.value.retry_after_seconds == 45


@pytest.mark.asyncio
async def test_context_overflow_never_dispatches():
    called = []
    provider = NvidiaNimProvider(
        api_key="test",
        planner_profile=PlannerModelProfile(context_tokens=2000),
        transport=httpx.MockTransport(
            lambda request: (called.append(request), httpx.Response(200))[1]
        ),
    )
    with pytest.raises(AIProviderError) as error:
        await provider.generate_text(model="test", request=AITextRequest("", "x" * 3000))
    assert error.value.category == "context_too_large" and not called


@pytest.mark.asyncio
async def test_readiness_caches_new_model_and_rejects_unsupported_format(monkeypatch):
    key = "openrouter:example/future:free"
    monkeypatch.setattr(
        settings,
        "smart_planner_model_profiles",
        {key: PlannerModelProfile(context_tokens=50000, output_format="json_schema")},
    )
    cache = {}
    coordinator = SimpleNamespace(
        cache_get=AsyncMock(side_effect=lambda key: cache.get(key)),
        cache_set=AsyncMock(side_effect=lambda key, value, **kwargs: cache.update({key: value})),
    )
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200, json={"data": [{"id": "example/future:free", "supported_parameters": []}]}
        )

    provider = OpenRouterPlannerProvider(
        api_key="test",
        data_allowed=True,
        allowed_providers=["test"],
        models=["example/future:free"],
        transport=httpx.MockTransport(handle),
    )
    result = await provider.readiness(coordinator)
    assert not result["example/future:free"]["compatible"]
    await provider.readiness(coordinator)
    assert len(calls) == 1


def test_unknown_model_is_not_assumed_compatible():
    with pytest.raises(AIProviderError):
        model_profile("nvidia_nim", "unregistered")


@pytest.mark.asyncio
async def test_endpoint_permission_error_is_not_a_safety_refusal():
    provider = OpenRouterPlannerProvider(
        api_key="test",
        data_allowed=True,
        allowed_providers=["test"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                403, json={"error": {"message": "endpoint not permitted"}}
            )
        ),
    )
    with pytest.raises(AIProviderError) as error:
        await provider.generate_text(
            model="google/gemma-4-31b-it:free", request=AITextRequest("", "")
        )
    assert error.value.category == "configuration_missing"
