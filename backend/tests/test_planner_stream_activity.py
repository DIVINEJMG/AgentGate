import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.domain.ai.providers import AIProviderError, AITextRequest
from app.infrastructure.ai.model_profiles import PlannerModelProfile
from app.infrastructure.ai.nvidia import NvidiaNimProvider
from app.infrastructure.ai.openrouter import OpenRouterPlannerProvider
from app.infrastructure.ai.streaming import collect_response
from app.runtime.smart_planner import CountedProvider


def response(model="model", finish="stop", refusal=False):
    chunks = [
        {"model": model, "choices": [{"delta": {"reasoning_content": "PRIVATE_REASONING"}}]},
        {
            "model": model,
            "choices": [{"delta": {"content": "{}", "refusal": refusal}, "finish_reason": finish}],
        },
    ]
    return httpx.Response(
        200, text="\n\n".join("data: " + json.dumps(c) for c in chunks) + "\n\ndata: [DONE]\n"
    )


@pytest.mark.asyncio
async def test_stream_progress_is_metadata_only_and_reasoning_is_discarded():
    progress = AsyncMock()
    result = await collect_response(response(), model="model", progress=progress)
    assert result["choices"][0]["message"]["content"] == "{}"
    assert "PRIVATE_REASONING" not in json.dumps(result)
    assert all(isinstance(call.args[0], int) for call in progress.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish,refusal,category",
    [
        (None, False, "invalid_provider_response"),
        ("length", False, "invalid_provider_response"),
        ("content_filter", False, "content_rejected"),
        ("stop", True, "content_rejected"),
    ],
)
async def test_partial_or_refused_stream_never_becomes_an_action(finish, refusal, category):
    with pytest.raises(AIProviderError) as error:
        await collect_response(response(finish=finish, refusal=refusal), model="model")
    assert error.value.category == category


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["nvidia", "openrouter"])
async def test_planner_streaming_preserves_adapter_validation_and_progress(adapter):
    model = "model" if adapter == "nvidia" else "model:free"
    profile = PlannerModelProfile(context_tokens=100000)
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return response(model=model)

    transport = httpx.MockTransport(handle)
    if adapter == "nvidia":
        provider = NvidiaNimProvider(
            coordinator_api_key="test", planner_profile=profile, transport=transport
        )
    else:
        provider = OpenRouterPlannerProvider(
            api_key="test",
            allowed_providers=["host"],
            data_allowed=True,
            transport=transport,
            profiles={model: profile},
        )
        provider.readiness = AsyncMock(
            return_value={model: {"compatible": True, "contextTokens": 100000}}
        )
    coordinator = SimpleNamespace(
        acquire_slot=AsyncMock(return_value="account-slot"),
        release_slot=AsyncMock(),
        reserve_limit=AsyncMock(return_value=True),
        reserve_units=AsyncMock(return_value=True),
        reserve_ai_budget=AsyncMock(return_value=-1),  # -1: every workload ceiling admitted
        settle_ai_budget=AsyncMock(return_value=True),
        cache_set=AsyncMock(),
    )
    attempt = SimpleNamespace(id=uuid4(), call_count=0, transport_success=False)
    counted = CountedProvider(
        provider, SimpleNamespace(commit=AsyncMock()), attempt, coordinator, uuid4()
    )
    result = await counted.generate_text(model=model, request=AITextRequest("", ""))
    assert bodies[0]["stream"] is True and result.text == "{}"
    metadata = json.loads(coordinator.cache_set.await_args.args[1])
    assert metadata["call"] == 1 and metadata["receivedBytes"] > 0
    assert set(metadata) == {"receivedAt", "receivedBytes", "call"}


@pytest.mark.asyncio
async def test_progress_outage_does_not_fail_an_otherwise_valid_response():
    provider = NvidiaNimProvider(
        coordinator_api_key="test",
        planner_profile=PlannerModelProfile(context_tokens=100000),
        transport=httpx.MockTransport(lambda request: response()),
    )
    coordinator = SimpleNamespace(
        acquire_slot=AsyncMock(return_value="account-slot"),
        release_slot=AsyncMock(),
        reserve_limit=AsyncMock(return_value=True),
        reserve_units=AsyncMock(return_value=True),
        reserve_ai_budget=AsyncMock(return_value=-1),  # -1: every workload ceiling admitted
        settle_ai_budget=AsyncMock(return_value=True),
        cache_set=AsyncMock(side_effect=ConnectionError("PRIVATE_ERROR")),
    )
    counted = CountedProvider(
        provider,
        SimpleNamespace(commit=AsyncMock()),
        SimpleNamespace(id=uuid4(), call_count=0, transport_success=False),
        coordinator,
        uuid4(),
    )
    assert (await counted.generate_text(model="model", request=AITextRequest("", ""))).text == "{}"
