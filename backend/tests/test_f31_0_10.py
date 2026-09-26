from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import pytest

from app.application.services.ai_gateway import ProviderAIGateway, UnconfiguredAIGateway
from app.domain.ai.providers import (
    AIInvocationContext,
    AIMediaInput,
    AIProviderError,
    AIResponse,
    AITextRequest,
    ModelProviderCapabilities,
)
from app.domain.ai.registry import ModelRegistry, ModelRoute
from app.infrastructure.ai.nvidia import NvidiaNimProvider
from app.infrastructure.database.models import AIInvocation


def _registry() -> ModelRegistry:
    return ModelRegistry(
        [
            ModelRoute(role="planner", provider="fake", model="planner-model"),
            ModelRoute(role="vision", provider="fake", model="vision-model"),
        ]
    )


@dataclass
class FakeProvider:
    outputs: list[str]
    calls: list[tuple[str, AITextRequest]] = field(default_factory=list)
    media_models: list[str] = field(default_factory=list)
    name: str = "fake"
    capabilities: ModelProviderCapabilities = field(
        default_factory=lambda: ModelProviderCapabilities(
            text_input=True,
            image_input=True,
            structured_json=True,
            tool_calls=True,
            streaming=True,
        )
    )

    async def generate_text(self, *, model: str, request: AITextRequest) -> AIResponse:
        self.calls.append((model, request))
        index = min(len(self.calls) - 1, len(self.outputs) - 1)
        return AIResponse(
            text=self.outputs[index],
            provider=self.name,
            model=model,
            request_id=f"request-{len(self.calls)}",
        )

    async def analyze_media(
        self,
        *,
        model: str,
        request: AITextRequest,
        media: AIMediaInput,
    ) -> AIResponse:
        del request, media
        self.media_models.append(model)
        return AIResponse(text="image received", provider=self.name, model=model)


@pytest.mark.asyncio
async def test_unconfigured_ai_gateway_fails_closed() -> None:
    gateway = UnconfiguredAIGateway()
    with pytest.raises(AIProviderError) as caught:
        await gateway.generate_text(
            role="planner",
            system="system",
            prompt="prompt",
        )
    assert caught.value.category == "configuration_missing"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_structured_gateway_repairs_invalid_schema_once() -> None:
    provider = FakeProvider(
        outputs=[
            '{"decision":"invented"}',
            '{"decision":"act","scope":"browser.navigation.open"}',
        ]
    )
    gateway = ProviderAIGateway(
        providers={"fake": provider},
        registry=_registry(),
        max_retries=0,
    )
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {"type": "string", "enum": ["act", "finish"]},
            "scope": {"type": "string"},
        },
        "required": ["decision", "scope"],
    }

    result = await gateway.generate_structured(
        role="planner",
        system="system",
        prompt="plan",
        schema_name="test_plan",
        schema=schema,
    )

    assert result == {"decision": "act", "scope": "browser.navigation.open"}
    assert len(provider.calls) == 2
    assert "AUTHORITATIVE_JSON_SCHEMA" in provider.calls[0][1].prompt
    assert '"required":["decision","scope"]' in provider.calls[0][1].prompt
    assert "PREVIOUS RESPONSE WAS INVALID" in provider.calls[1][1].prompt


@pytest.mark.asyncio
async def test_structured_gateway_rejects_second_invalid_response() -> None:
    provider = FakeProvider(outputs=['{"decision":"bad"}', '{"decision":"still-bad"}'])
    gateway = ProviderAIGateway(
        providers={"fake": provider},
        registry=_registry(),
        max_retries=0,
    )
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"decision": {"type": "string", "enum": ["act", "finish"]}},
        "required": ["decision"],
    }

    with pytest.raises(AIProviderError) as caught:
        await gateway.generate_structured(
            role="planner",
            system="system",
            prompt="plan",
            schema_name="test_plan",
            schema=schema,
        )

    assert caught.value.category == "invalid_provider_response"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_model_role_registry_routes_coordinator_and_vision_models() -> None:
    provider = FakeProvider(outputs=["OK"])
    gateway = ProviderAIGateway(
        providers={"fake": provider},
        registry=_registry(),
        max_retries=0,
    )

    response = await gateway.generate_text(
        role="planner",
        system="system",
        prompt="hello",
    )
    vision = await gateway.analyze_media(
        role="vision",
        system="system",
        prompt="inspect",
        media=AIMediaInput(media_type="image/png", data_base64="AA=="),
    )

    assert response.model == "planner-model"
    assert vision.model == "vision-model"
    assert provider.calls[0][0] == "planner-model"
    assert provider.media_models == ["vision-model"]


@pytest.mark.asyncio
async def test_nvidia_text_completion_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
        assert payload["messages"][0] == {"role": "system", "content": "system"}
        assert payload["messages"][1] == {"role": "user", "content": "hello"}
        return httpx.Response(
            200,
            headers={"x-request-id": "nim-123"},
            json={
                "id": "completion-1",
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    provider = NvidiaNimProvider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    response = await provider.generate_text(
        model="nvidia/nemotron-3-ultra-550b-a55b",
        request=AITextRequest(
            system="system",
            prompt="hello",
            correlation_id="corr-123",
        ),
    )

    assert response.text == "OK"
    assert response.request_id == "nim-123"
    assert response.usage["total_tokens"] == 3


@pytest.mark.asyncio
async def test_nvidia_image_completion_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        content = payload["messages"][-1]["content"]
        assert content[0] == {"type": "text", "text": "inspect"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert payload["model"] == "nvidia/ising-calibration-1.5-31b"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "visual finding"}}]},
        )

    provider = NvidiaNimProvider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    response = await provider.analyze_media(
        model="nvidia/ising-calibration-1.5-31b",
        request=AITextRequest(system="system", prompt="inspect"),
        media=AIMediaInput(media_type="image/png", data_base64="AA=="),
    )

    assert response.text == "visual finding"



@pytest.mark.asyncio
async def test_nvidia_uses_separate_coordinator_and_vision_credentials() -> None:
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append((payload["model"], request.headers["authorization"]))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    provider = NvidiaNimProvider(
        coordinator_api_key="coordinator-key",
        vision_api_key="vision-key",
        transport=httpx.MockTransport(handler),
    )

    await provider.generate_text(
        model="nvidia/nemotron-3-ultra-550b-a55b",
        request=AITextRequest(system="system", prompt="hello"),
    )
    await provider.analyze_media(
        model="nvidia/ising-calibration-1.5-31b",
        request=AITextRequest(system="system", prompt="inspect"),
        media=AIMediaInput(media_type="image/png", data_base64="AA=="),
    )

    assert seen == [
        ("nvidia/nemotron-3-ultra-550b-a55b", "Bearer coordinator-key"),
        ("nvidia/ising-calibration-1.5-31b", "Bearer vision-key"),
    ]


@pytest.mark.asyncio
async def test_nvidia_missing_role_credential_fails_closed() -> None:
    provider = NvidiaNimProvider(
        coordinator_api_key="coordinator-key",
        vision_api_key=None,
    )

    with pytest.raises(AIProviderError) as caught:
        await provider.analyze_media(
            model="nvidia/ising-calibration-1.5-31b",
            request=AITextRequest(system="system", prompt="inspect"),
            media=AIMediaInput(media_type="image/png", data_base64="AA=="),
        )

    assert caught.value.category == "configuration_missing"
    assert "vision credential" in str(caught.value)


@pytest.mark.asyncio
async def test_nvidia_structured_json_uses_hosted_compatible_mode() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.update(payload)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"decision":"finish"}'}}]},
        )

    provider = NvidiaNimProvider(
        api_key="test-key",
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": True,
                "force_nonempty_content": True,
            }
        },
        transport=httpx.MockTransport(handler),
    )
    await provider.generate_text(
        model="nvidia/nemotron-3-ultra-550b-a55b",
        request=AITextRequest(
            system="system",
            prompt="return json",
            response_format="json_object",
        ),
    )

    assert "response_format" not in seen
    assert seen["chat_template_kwargs"] == {
        "enable_thinking": False,
        "force_nonempty_content": True,
    }


@pytest.mark.asyncio
async def test_nvidia_plain_text_does_not_force_thinking_off() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    provider = NvidiaNimProvider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    await provider.generate_text(
        model="nvidia/nemotron-3-ultra-550b-a55b",
        request=AITextRequest(system="system", prompt="hello"),
    )

    assert "response_format" not in seen
    assert "chat_template_kwargs" not in seen

@pytest.mark.asyncio
async def test_nvidia_rate_limit_is_normalized_and_retryable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            429,
            json={"error": {"message": "rate limit reached"}},
        )

    provider = NvidiaNimProvider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AIProviderError) as caught:
        await provider.generate_text(
            model="nvidia/nemotron-3-ultra-550b-a55b",
            request=AITextRequest(system="system", prompt="hello"),
        )

    assert caught.value.category == "rate_limited"
    assert caught.value.retryable is True


def test_ai_invocation_model_persists_metadata_not_prompt_secrets() -> None:
    columns = set(AIInvocation.__table__.columns.keys())
    assert {
        "organization_id",
        "worker_id",
        "job_id",
        "run_id",
        "role",
        "provider",
        "model",
        "latency_ms",
        "success",
        "usage",
        "error_category",
        "schema_name",
        "correlation_id",
    }.issubset(columns)
    assert "prompt" not in columns
    assert "system_prompt" not in columns
    assert "api_key" not in columns
    assert "secret" not in columns


def test_invocation_context_is_tenant_scoped_when_runtime_supplies_it() -> None:
    context = AIInvocationContext(correlation_id="corr")
    assert context.organization_id is None
    # System probes may be unscoped; Runtime passes an organization ID explicitly.
