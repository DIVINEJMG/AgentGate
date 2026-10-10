from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.ai_gateway import ProviderAIGateway, UnconfiguredAIGateway
from app.bootstrap.settings import Settings, settings
from app.domain.ai.providers import AIGateway
from app.domain.ai.registry import ModelRegistry, ModelRoute
from app.infrastructure.ai.nvidia import NvidiaNimProvider
from app.infrastructure.ai.telemetry import (
    DurableAIInvocationRecorder,
    SQLAlchemyAIInvocationRecorder,
)


def model_registry_from_settings(config: Settings = settings) -> ModelRegistry:
    routes = [ModelRoute(role="planner", provider=raw.partition(":")[0],
        model=config.ai_coordinator_model if raw.partition(":")[2] == "configured" else raw.partition(":")[2])
        for raw in config.ai_complex_routes] if config.smart_planner_enabled else []
    return ModelRegistry(
        routes + [
            ModelRoute(
                role="conversation",
                provider=config.ai_provider,
                model=config.ai_coordinator_model,
            ),
            ModelRoute(
                role="intent",
                provider=config.ai_provider,
                model=config.ai_coordinator_model,
            ),
            ModelRoute(
                role="planner",
                provider=config.ai_provider,
                model=config.ai_coordinator_model,
            ),
            ModelRoute(
                role="vision",
                provider=config.ai_provider,
                model=config.ai_vision_model,
            ),
        ]
    )


def _secret_value(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value().strip()
    return value or None


def ai_gateway_from_settings(
    config: Settings = settings,
    *,
    session: AsyncSession | None = None,
    planner: bool = False,
    workload: str = "interactive",
    purpose: str = "human_request",
) -> AIGateway:
    if not config.ai_enabled:
        return UnconfiguredAIGateway("Aduoryn AI is disabled for this environment.")
    legacy_key = _secret_value(config.ai_provider_api_key)
    coordinator_key = _secret_value(config.ai_coordinator_api_key) or legacy_key
    vision_key = _secret_value(config.ai_vision_api_key) or legacy_key
    if not coordinator_key and not vision_key and not (config.smart_planner_enabled and
            (config.openrouter_api_key or config.groq_api_key or config.hf_token or config.ai_text_api_keys)):
        return UnconfiguredAIGateway("AI provider credentials are not configured.")
    if config.ai_provider != "nvidia_nim":
        return UnconfiguredAIGateway(
            f"AI provider {config.ai_provider} is not supported by this deployment."
        )

    provider = NvidiaNimProvider(
        coordinator_api_key=coordinator_key,
        vision_api_key=vision_key,
        base_url=config.ai_provider_base_url,
        timeout_seconds=config.runtime_planner_timeout_seconds if planner else config.ai_timeout_seconds,
    )
    if planner:
        recorder = DurableAIInvocationRecorder()
    else:
        recorder = SQLAlchemyAIInvocationRecorder(session) if session is not None else None
    gateway = ProviderAIGateway(
        providers={provider.name: provider},
        registry=model_registry_from_settings(config),
        recorder=recorder,
        max_retries=config.ai_max_retries,
        default_max_output_tokens=config.ai_max_output_tokens,
    )
    if config.smart_planner_enabled and not planner:
        from app.application.services.ordered_ai import OrderedTextGateway
        from app.infrastructure.ai.text_routes import snapshot_routes, text_gateway

        def build(selected, selected_purpose):
            return OrderedTextGateway(
                legacy=gateway, routes=snapshot_routes(config, selected),
                factory=lambda route, role, context: text_gateway(route, role, context, config, recorder, selected),
                budget_seconds=(config.runtime_planner_timeout_seconds if selected == "complex"
                                else config.ai_interactive_timeout_seconds),
                attempt_seconds=(config.runtime_planner_timeout_seconds if selected == "complex"
                                 else config.ai_interactive_attempt_seconds),
                workload=selected, purpose=selected_purpose, workload_factory=build,
            )
        return build(workload, purpose)
    return gateway
