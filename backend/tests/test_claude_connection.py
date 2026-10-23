"""Claude through the native Messages API: structured outputs when the schema fits, a cached
system prompt, effort from the profile and no temperature; clear errors so fallback moves on."""
from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr

from audoryn_shared.application.services.integration_foundation import IntegrationTaskDraft
from audoryn_shared.bootstrap.settings import Settings
from audoryn_shared.domain.ai.providers import AIProviderError, AITextRequest
from audoryn_shared.domain.conversation.intent import WorkerCommandIntent
from audoryn_shared.domain.workforce.drafts import WorkerDraft
from audoryn_shared.infrastructure.ai.anthropic_messages import (
    UNSUPPORTED_KEYWORDS,
    AnthropicMessagesProvider,
    claude_schema,
)
from audoryn_shared.infrastructure.ai.connections import (
    DEFAULT_CONNECTIONS,
    route_credential,
    snapshot_connection,
)
from audoryn_shared.infrastructure.ai.model_profiles import DEFAULT_PROFILES
from audoryn_shared.infrastructure.ai.text_routes import transport_for_text_route

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "maxLength": 40}}, "required": ["answer"]}


def connection() -> Any:
    return DEFAULT_CONNECTIONS["anthropic"].model_copy(update={"enabled": True, "data_allowed": True, "billing_allowed": True})


def claude(handler: Any, model: str = HAIKU, credential: str | None = "sk-ant-test") -> AnthropicMessagesProvider:
    return AnthropicMessagesProvider(name="anthropic", connection=connection(), credential=credential,
                                     profile=DEFAULT_PROFILES[f"anthropic:{model}"], timeout_seconds=60,
                                     transport=httpx.MockTransport(handler))


def message(text: str = '{"answer":"ok"}', *, model: str = HAIKU, stop: str = "end_turn") -> dict[str, Any]:
    return {"type": "message", "id": "msg_1", "model": model, "stop_reason": stop,
            "content": [{"type": "thinking", "thinking": "private"}, {"type": "text", "text": text}],
            "usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0, "output_tokens": 50}}


def request(**changes: Any) -> AITextRequest:
    return AITextRequest(**{"system": "You are Audoryn.", "prompt": "Hello", "response_format": "json_object",
                            "json_schema": SCHEMA, "schema_name": "answer", **changes})


@pytest.mark.asyncio
async def test_the_request_uses_structured_outputs_a_cached_system_prompt_and_effort() -> None:
    sent: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(200, json=message())

    response = await claude(handler).generate_text(model=HAIKU, request=request())
    [req] = sent
    body = json.loads(req.content)
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-ant-test" and req.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in req.headers
    assert body["model"] == HAIKU and body["max_tokens"] == 8192 and "temperature" not in body
    assert body["system"] == [{"type": "text", "text": "You are Audoryn.", "cache_control": {"type": "ephemeral"}}]
    assert body["messages"] == [{"role": "user", "content": "Hello"}]
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": {
        "type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}}
    assert response.text == '{"answer":"ok"}'  # thinking is never returned
    assert response.usage == {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050,
                              "cache_read_tokens": 900, "cache_write_tokens": 0}


def test_sonnet_plans_at_medium_effort_with_room_for_thinking() -> None:
    body = claude(lambda _: httpx.Response(200), model=SONNET).body(SONNET, request(response_format="text"))
    assert body["output_config"] == {"effort": "medium"} and body["max_tokens"] == 16384


def test_schemas_are_reduced_to_what_claude_accepts_or_left_to_the_prompt() -> None:
    assert claude_schema({"type": "object", "properties": {"n": {"type": "integer", "minimum": 1},
        "tags": {"type": "array", "items": {"type": "string", "pattern": "x+"}, "minItems": 3, "maxItems": 9},
        "kind": {"oneOf": [{"type": "string", "format": "email"}, {"type": "string", "format": "custom"}]}}}) == {
        "type": "object", "additionalProperties": False, "properties": {"n": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "kind": {"anyOf": [{"type": "string", "format": "email"}, {"type": "string"}]}}}
    # A described reference: Pydantic's single-entry allOf becomes the reference itself.
    assert cast(Any, claude_schema({"type": "object", "properties": {"a": {"allOf": [{"$ref": "#/$defs/A"}], "description": "d"}},
                          "$defs": {"A": {"type": "string"}}}))["properties"]["a"] == {"$ref": "#/$defs/A", "description": "d"}
    assert claude_schema({"type": "object", "properties": {"free": {"type": "object", "additionalProperties": True}}}) is None
    assert claude_schema({"type": "object", "properties": {"node": {"$ref": "#/$defs/N"}},
                          "$defs": {"N": {"type": "object", "properties": {"next": {"$ref": "#/$defs/N"}}}}}) is None


@pytest.mark.parametrize("model", [WorkerCommandIntent, IntegrationTaskDraft, WorkerDraft])
def test_audoryn_schemas_convert_or_fall_back_without_errors(model: Any) -> None:
    converted = claude_schema(model.model_json_schema())

    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {key for value in node.values() for key in keys(value)}
        return {key for value in node for key in keys(value)} if isinstance(node, list) else set()

    assert converted is None or not keys(converted) & UNSUPPORTED_KEYWORDS


@pytest.mark.asyncio
async def test_a_streamed_plan_is_assembled_and_reports_progress() -> None:
    events = [
        {"type": "message_start", "message": {"id": "msg_2", "model": SONNET, "usage": {"input_tokens": 10, "cache_read_input_tokens": 500}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "secret"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": '{"answer":'}},
        {"type": "ping"},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": '"planned"}'}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 30}},
        {"type": "message_stop"},
    ]
    stream = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    progress: list[int] = []
    provider = claude(lambda _: httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"}), model=SONNET)

    async def seen(received: int) -> None:
        progress.append(received)

    cast(Any, provider).planner_progress = seen
    response = await provider.generate_text(model=SONNET, request=request(stream=True))
    assert response.text == '{"answer":"planned"}' and "secret" not in response.text
    assert response.usage["prompt_tokens"] == 510 and response.usage["completion_tokens"] == 30
    assert progress and progress == sorted(progress)


@pytest.mark.asyncio
async def test_a_rejected_schema_is_retried_once_with_the_schema_in_the_prompt_only() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        if len(bodies) == 1:
            return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error",
                                                                        "message": "output_config.format.schema: unsupported"}})
        return httpx.Response(200, json=message())

    response = await claude(handler).generate_text(model=HAIKU, request=request())
    assert response.text == '{"answer":"ok"}'
    assert "format" in bodies[0]["output_config"] and "format" not in bodies[1]["output_config"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "error", "category", "account_scoped", "retryable"), [
    (400, {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."},
     "quota_exhausted", True, False),
    (429, {"type": "rate_limit_error", "message": "limits", "details": {"error_code": "enforced_spend_limit_reached"}},
     "quota_exhausted", True, False),
    (429, {"type": "rate_limit_error", "message": "Number of request tokens has exceeded your rate limit"},
     "rate_limited", True, True),
    (401, {"type": "authentication_error", "message": "invalid x-api-key"}, "authentication_failed", True, False),
    (529, {"type": "overloaded_error", "message": "Overloaded"}, "provider_unavailable", False, True),
])
async def test_account_problems_are_clear_so_the_next_model_is_tried(status: int, error: dict[str, Any], category: str,
                                                                    account_scoped: bool, retryable: bool) -> None:
    provider = claude(lambda _: httpx.Response(status, json={"type": "error", "error": error}))
    with pytest.raises(AIProviderError) as raised:
        await provider.generate_text(model=HAIKU, request=request(response_format="text"))
    assert (raised.value.category, raised.value.account_scoped, raised.value.retryable) == (category, account_scoped, retryable)
    assert "credit" not in str(raised.value) or category == "quota_exhausted"


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "category", "reason"), [
    (message(stop="refusal"), "content_rejected", None),
    (message(stop="max_tokens"), "invalid_provider_response", "truncated_output"),
    (message(text=""), "invalid_provider_response", "empty_output"),
    (message(model="claude-opus-5-5"), "invalid_provider_response", "model_mismatch"),
])
async def test_incomplete_or_unexpected_answers_are_rejected(payload: dict[str, Any], category: str, reason: str | None) -> None:
    with pytest.raises(AIProviderError) as raised:
        await claude(lambda _: httpx.Response(200, json=payload)).generate_text(model=HAIKU, request=request())
    assert raised.value.category == category and raised.value.rejection_reason == reason


@pytest.mark.asyncio
async def test_without_a_key_the_route_is_skipped_before_any_request() -> None:
    sent: list[httpx.Request] = []
    provider = claude(lambda req: sent.append(req) or httpx.Response(200, json=message()), credential=None)
    with pytest.raises(AIProviderError) as raised:
        await provider.generate_text(model=HAIKU, request=request())
    assert raised.value.category == "configuration_missing" and raised.value.account_scoped and not sent


def test_routes_pick_the_claude_transport_and_its_own_key() -> None:
    config = Settings.model_construct(anthropic_api_key=SecretStr("sk-ant-test"),
                                      ai_text_connections={"anthropic": connection()})
    route = snapshot_connection({"provider": "anthropic", "model": HAIKU, "profile": DEFAULT_PROFILES[f"anthropic:{HAIKU}"].model_dump()}, config)
    assert route["endpoint"] == "https://api.anthropic.com/v1/messages" and route["requestedModel"] == HAIKU
    assert route_credential(route, config) == "sk-ant-test"
    adapter, credential, profile = transport_for_text_route(route, config)
    assert isinstance(adapter, AnthropicMessagesProvider) and credential == "sk-ant-test"
    assert profile.request_parameters == {"reasoning_effort": "low"}
    assert "sk-ant-test" not in json.dumps(route)


@pytest.mark.asyncio
async def test_a_free_form_schema_goes_through_the_prompt_and_is_still_validated() -> None:
    from audoryn_shared.application.services.ai_gateway import ProviderAIGateway
    from audoryn_shared.domain.ai.registry import ModelRegistry, ModelRoute

    open_schema = {"type": "object", "properties": {"arguments": {"type": "object", "additionalProperties": True}},
                   "required": ["arguments"], "additionalProperties": False}
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=message('{"arguments": {"repo": "acme/api"}}'))

    gateway = ProviderAIGateway(providers={"anthropic": cast(Any, claude(handler))},
                                registry=ModelRegistry([ModelRoute(role="intent", provider="anthropic", model=HAIKU)]),
                                max_retries=0)
    result = await gateway.generate_structured(role="intent", system="s", prompt="p", schema_name="intent", schema=open_schema)
    assert result == {"arguments": {"repo": "acme/api"}}
    assert "format" not in bodies[0].get("output_config", {}) and "AUTHORITATIVE_JSON_SCHEMA" in bodies[0]["messages"][0]["content"]


# --- Claude through OpenRouter -----------------------------------------------------------------

OR_HAIKU, OR_SONNET = "anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5"


def openrouter(handler: Any, model: str = OR_HAIKU) -> Any:
    from audoryn_shared.infrastructure.ai.openrouter_claude import OpenRouterClaudeProvider

    conn = DEFAULT_CONNECTIONS["openrouter_claude"].model_copy(update={"enabled": True, "data_allowed": True, "billing_allowed": True})
    return OpenRouterClaudeProvider(name="openrouter_claude", connection=conn, credential="sk-or-test",
                                    profile=DEFAULT_PROFILES[f"openrouter_claude:{model}"], timeout_seconds=60,
                                    transport=httpx.MockTransport(handler))


def completion(text: str = '{"answer":"ok"}', model: str = OR_HAIKU, finish: str = "stop") -> dict[str, Any]:
    return {"id": "gen-1", "model": model, "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 40, "total_tokens": 1040}}


@pytest.mark.asyncio
async def test_openrouter_requests_go_only_to_anthropic_with_caching_effort_and_no_temperature() -> None:
    sent: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(200, json=completion(model=OR_HAIKU + "-20261007"))

    response = await openrouter(handler).generate_text(model=OR_HAIKU, request=request())
    [req] = sent
    body = json.loads(req.content)
    assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-or-test"
    assert body["model"] == OR_HAIKU and "temperature" not in body and body["max_tokens"] == 8192
    assert body["provider"] == {"only": ["anthropic"], "allow_fallbacks": False, "data_collection": "deny"}
    assert body["messages"][0] == {"role": "system", "content": [
        {"type": "text", "text": "You are Audoryn.", "cache_control": {"type": "ephemeral"}}]}
    assert body["reasoning"] == {"effort": "low", "exclude": True}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"]["properties"] == {"answer": {"type": "string"}}
    assert response.text == '{"answer":"ok"}' and response.usage["prompt_tokens"] == 1000  # a dated variant is accepted


@pytest.mark.asyncio
async def test_openrouter_sonnet_streams_a_plan_at_medium_effort() -> None:
    chunks = [
        {"id": "gen-2", "model": OR_SONNET, "choices": [{"delta": {"content": '{"answer":'}}]},
        {"id": "gen-2", "model": OR_SONNET, "choices": [{"delta": {"content": '"planned"}'}, "finish_reason": "stop"}]},
        {"id": "gen-2", "model": OR_SONNET, "choices": [], "usage": {"prompt_tokens": 500, "completion_tokens": 20, "total_tokens": 520}},
    ]
    stream = ": OPENROUTER PROCESSING\n\n" + "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    response = await openrouter(handler, OR_SONNET).generate_text(model=OR_SONNET, request=request(stream=True))
    assert response.text == '{"answer":"planned"}' and response.usage["completion_tokens"] == 20
    assert bodies[0]["reasoning"]["effort"] == "medium" and bodies[0]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "category"), [(402, "quota_exhausted"), (401, "authentication_failed"), (429, "rate_limited")])
async def test_openrouter_credit_or_key_problems_move_on_to_the_next_model(status: int, category: str) -> None:
    with pytest.raises(AIProviderError) as raised:
        await openrouter(lambda _: httpx.Response(status, json={"error": {"message": "x"}})).generate_text(model=OR_HAIKU, request=request())
    assert raised.value.category == category and raised.value.account_scoped


def test_openrouter_routes_use_the_openrouter_key() -> None:
    from audoryn_shared.infrastructure.ai.openrouter_claude import OpenRouterClaudeProvider

    conn = DEFAULT_CONNECTIONS["openrouter_claude"].model_copy(update={"enabled": True, "data_allowed": True, "billing_allowed": True})
    config = Settings.model_construct(openrouter_api_key=SecretStr("sk-or-test"), ai_text_connections={"openrouter_claude": conn})
    route = snapshot_connection({"provider": "openrouter_claude", "model": OR_SONNET}, config)
    assert route["endpoint"] == "https://openrouter.ai/api/v1/chat/completions"
    adapter, credential, profile = transport_for_text_route(route, config)
    assert isinstance(adapter, OpenRouterClaudeProvider) and credential == "sk-or-test"
    assert profile.request_parameters == {"reasoning_effort": "medium"}
