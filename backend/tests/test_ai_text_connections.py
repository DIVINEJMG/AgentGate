import json
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from app.application.services.ai_gateway import ProviderAIGateway
from app.bootstrap.settings import Settings
from app.domain.ai.providers import AIProviderError, AITextRequest
from app.domain.ai.registry import ModelRegistry, ModelRoute
from app.infrastructure.ai.connections import (
    DEFAULT_CONNECTIONS,
    route_account_name,
    route_credential,
    snapshot_connection,
)
from app.infrastructure.ai.model_profiles import DEFAULT_PROFILES
from app.infrastructure.ai.openai_chat import ConfiguredChatProvider, groq_strict_compatible
from app.infrastructure.ai.text_routes import snapshot_routes, transport_for_text_route
from scripts.benchmark_ai_common import CANDIDATES

STRICT = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def connection(name):
    return DEFAULT_CONNECTIONS[name].model_copy(
        update={"enabled": True, "data_allowed": True, "billing_allowed": True}
    )


def provider(route, handler):
    name, model = route.split(":", 1)
    return ConfiguredChatProvider(
        name=name,
        connection=connection(name),
        credential="test-secret",
        profile=DEFAULT_PROFILES[route],
        timeout_seconds=250,
        transport=httpx.MockTransport(handler),
    ), model


def envelope(model, content='{"answer":"ok"}', finish="stop"):
    return {"model": model, "choices": [{"finish_reason": finish, "message": {"content": content}}]}


@pytest.mark.parametrize("route", CANDIDATES)
@pytest.mark.asyncio
async def test_exact_host_and_model_with_no_hidden_fallback(route):
    captured = []

    def handler(request):
        captured.append(request)
        # HF may return its canonical Hub ID without the requested provider suffix.
        return httpx.Response(200, json=envelope(route.split(":", 1)[1]))

    adapter, model = provider(route, handler)
    result = await adapter.generate_text(
        model=model, request=AITextRequest("authority", "untrusted evidence")
    )
    body = json.loads(captured[0].content)
    expected = (
        model if adapter.name == "groq" else model + ":" + adapter.connection.hosting_provider
    )
    assert body["model"] == expected
    assert str(captured[0].url) == adapter.connection.base_url + "/chat/completions"
    assert body["messages"][0]["content"] == "authority"
    assert "provider" not in body and "models" not in body
    assert result.provider == adapter.name and result.model == model
    assert len(captured) == 1


def test_strict_schema_eligibility_does_not_modify_authority_or_optional_fields():
    adapter, model = provider(CANDIDATES[0], lambda _: None)
    request = AITextRequest(
        "", "JSON", response_format="json_object", json_schema=STRICT, stream=True
    )
    strict = adapter.body(model, request)
    assert strict["response_format"]["json_schema"]["schema"] is STRICT
    assert strict["stream"] is False and "stream_options" not in strict
    for schema in (
        {"type": "object"},
        {**STRICT, "required": []},
        {**STRICT, "properties": {"answer": {"type": "string", "minLength": 1}}},
    ):
        before = json.dumps(schema)
        assert not groq_strict_compatible(schema)
        assert adapter.body(model, replace(request, json_schema=schema))["response_format"] == {
            "type": "json_object"
        }
        assert json.dumps(schema) == before


@pytest.mark.parametrize(
    "status,category",
    [
        (401, "authentication_failed"),
        (402, "quota_exhausted"),
        (429, "rate_limited"),
        (404, "model_not_found"),
        (504, "timeout"),
        (503, "provider_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_errors_are_classified_without_transport_retry_or_secret_body(status, category):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"Retry-After": "7"}, json={"error": "PRIVATE_INPUT secret"}
        )

    adapter, model = provider(CANDIDATES[0], handler)
    with pytest.raises(AIProviderError) as error:
        await adapter.generate_text(model=model, request=AITextRequest("", ""))
    assert error.value.category == category
    assert error.value.status_code == status and error.value.retry_after_seconds == 7
    assert "PRIVATE_INPUT" not in str(error.value) and "test-secret" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload,category",
    [
        (envelope("different/model"), "invalid_provider_response"),
        (envelope("qwen/qwen3.8-27b", finish="length"), "invalid_provider_response"),
        (envelope("qwen/qwen3.8-27b", content=""), "invalid_provider_response"),
        (envelope("qwen/qwen3.8-27b", finish="content_filter"), "content_rejected"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_refused_response_never_accepted(payload, category):
    adapter, model = provider(CANDIDATES[0], lambda _: httpx.Response(200, json=payload))
    with pytest.raises(AIProviderError) as error:
        await adapter.generate_text(model=model, request=AITextRequest("", ""))
    assert error.value.category == category


@pytest.mark.asyncio
async def test_native_schema_response_still_receives_local_validation_and_one_correction():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=envelope("qwen/qwen3.8-27b", '{"invented":true}'))

    adapter, model = provider(CANDIDATES[0], handler)
    gateway = ProviderAIGateway(
        providers={adapter.name: adapter},
        max_retries=0,
        registry=ModelRegistry([ModelRoute(role="planner", provider=adapter.name, model=model)]),
    )
    with pytest.raises(AIProviderError):
        await gateway.generate_structured(
            role="planner", system="", prompt="JSON", schema_name="test", schema=STRICT
        )
    assert len(calls) == 2


def test_snapshot_pins_endpoint_host_profile_and_shared_account_identity():
    from pydantic import SecretStr

    config = Settings.model_construct(
        ai_complex_routes=CANDIDATES,
        groq_api_key=SecretStr("groq-test"),
        hf_token=SecretStr("hf-test"),
    )
    config.ai_text_connections = {name: connection(name) for name in DEFAULT_CONNECTIONS}
    routes = snapshot_routes(config, "complex")
    assert routes[1]["requestedModel"] == "Qwen/Qwen2.5-7B-Instruct:featherless-ai"
    assert route_account_name(routes[1], config) == route_account_name(routes[2], config)
    assert route_credential(routes[1], config) == "hf-test"
    config.ai_text_connections["hf_featherless"] = connection("hf_novita")
    adapter, credential, _ = transport_for_text_route(routes[1], config)
    assert isinstance(adapter, ConfiguredChatProvider)
    assert adapter.connection.hosting_provider == "featherless-ai" and credential == "hf-test"
    assert "hf-test" not in json.dumps(routes)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:password@example.com",
        "https://127.0.0.1",
        "https://api.example.com?token=secret",
        "https://internal.local",
    ],
)
def test_config_rejects_private_or_credential_bearing_endpoints(url):
    with pytest.raises(ValidationError):
        DEFAULT_CONNECTIONS["groq"].model_copy().model_validate(
            {**DEFAULT_CONNECTIONS["groq"].model_dump(), "base_url": url}
        )


def test_workload_priorities_with_disabled_connection_defaults_and_unchanged_budgets():
    config = Settings.model_construct()
    assert not any(c.enabled for c in config.ai_text_connections.values())
    assert config.ai_interactive_routes[:2] == ["nvidia_nim:openai/gpt-oss-20b", CANDIDATES[0]]
    assert config.ai_complex_routes[0] == CANDIDATES[0]
    assert not set(CANDIDATES[1:]).intersection(config.ai_interactive_routes + config.ai_complex_routes)
    assert (
        config.ai_interactive_timeout_seconds,
        config.ai_interactive_attempt_seconds,
        config.runtime_planner_timeout_seconds,
    ) == (1500, 250, 2000)
    route = snapshot_connection({"provider": "groq", "model": "qwen/qwen3.8-27b"}, config)
    with pytest.raises(AIProviderError, match="disabled"):
        transport_for_text_route(route, config)


def test_revoked_data_consent_blocks_an_existing_route_snapshot():
    config = Settings.model_construct(ai_text_connections={"groq": connection("groq")})
    route = snapshot_connection({"provider": "groq", "model": "qwen/qwen3.8-27b"}, config)
    config.ai_text_connections["groq"].data_allowed = False
    with pytest.raises(AIProviderError, match="data handling"):
        transport_for_text_route(route, config)


def test_hf_auto_model_routing_and_unapproved_billing_are_blocked():
    adapter, _ = provider(CANDIDATES[1], lambda _: None)
    adapter.connection.billing_allowed = False
    with pytest.raises(AIProviderError, match="billing"):
        adapter.validate_request(model="Qwen/Qwen2.5-7B-Instruct", request=AITextRequest("", ""))
    adapter.connection.billing_allowed = True
    with pytest.raises(AIProviderError, match="suffixes"):
        adapter.validate_request(
            model="Qwen/Qwen2.5-7B-Instruct:auto", request=AITextRequest("", "")
        )


@pytest.mark.asyncio
async def test_new_connection_fallback_uses_existing_ordered_selector():
    from app.application.services.ordered_ai import OrderedTextGateway

    routes = [
        {"provider": "groq", "model": "qwen/qwen3.8-27b"},
        {"provider": "hf_featherless", "model": "Qwen/Qwen2.5-7B-Instruct"},
    ]
    calls = []

    def factory(route, role, context):
        name, model = route["provider"], route["model"]

        def handler(request):
            calls.append(name)
            return httpx.Response(503 if name == "groq" else 200, json=envelope(model))

        adapter, _ = provider(f"{name}:{model}", handler)
        return ProviderAIGateway(
            providers={name: adapter},
            max_retries=0,
            registry=ModelRegistry([ModelRoute(role=role, provider=name, model=model)]),
        )

    selector = OrderedTextGateway(
        legacy=None, routes=routes, factory=factory, budget_seconds=1500, attempt_seconds=250
    )
    result = await selector.generate_structured(
        role="intent", system="exact authority", prompt="JSON", schema_name="test", schema=STRICT
    )
    assert result == {"answer": "ok"} and calls == ["groq", "hf_featherless"]


def test_connection_migration_compiles_upgrade_and_downgrade_without_database():
    import importlib.util
    from io import StringIO
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / "migrations/versions/0013_ai_connections.py"
    spec = importlib.util.spec_from_file_location("connections_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert migration.down_revision == "0012_ai_workloads"
    for table in ("ai_invocations", "planner_attempts"):
        assert f"ALTER TABLE {table} ADD COLUMN transport_identity JSONB" in sql
        assert f"ALTER TABLE {table} DROP COLUMN transport_identity" in sql
    assert "NOT NULL" in sql and "'{}'::jsonb" in sql


@pytest.mark.asyncio
async def test_transport_identity_is_persisted_by_invocation_recorder():
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domain.ai.providers import AIInvocationContext
    from app.infrastructure.ai.telemetry import (
        SQLAlchemyAIInvocationRecorder,
        TransportInvocationRecorder,
    )

    rows = []
    session = SimpleNamespace(add=rows.append, flush=AsyncMock())
    route = snapshot_connection(
        {"provider": "groq", "model": "qwen/qwen3.8-27b"}, Settings.model_construct()
    )
    from app.infrastructure.ai.connections import transport_identity

    recorder = TransportInvocationRecorder(
        SQLAlchemyAIInvocationRecorder(cast(AsyncSession, session)), transport_identity(route)
    )
    await recorder.start(
        context=AIInvocationContext(),
        role="planner",
        provider="groq",
        model="qwen/qwen3.8-27b",
        schema_name="test",
    )
    assert rows[0].transport_identity == {
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "hostingProvider": "groq",
        "requestedModel": "qwen/qwen3.8-27b",
    }
