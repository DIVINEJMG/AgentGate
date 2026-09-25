from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.ai_gateway import ProviderAIGateway, UnconfiguredAIGateway
from app.bootstrap.settings import Settings, settings
from app.domain.ai.providers import AIGateway
from app.domain.ai.registry import ModelRegistry, ModelRoute
from app.infrastructure.ai.nvidia import NvidiaNimProvider
from app.infrastructure.ai.telemetry import SQLAlchemyAIInvocationRecorder


def model_registry_from_settings(config: Settings = settings) -> ModelRegistry:
    return ModelRegistry(
        [
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


def ai_gateway_from_settings(
    config: Settings = settings,
    *,
    session: AsyncSession | None = None,
) -> AIGateway:
    if not config.ai_enabled:
        return UnconfiguredAIGateway("Aduoryn AI is disabled for this environment.")
    if config.ai_provider_api_key is None:
        return UnconfiguredAIGateway("AI provider credential is not configured.")
    api_key = config.ai_provider_api_key.get_secret_value().strip()
    if not api_key:
        return UnconfiguredAIGateway("AI provider credential is not configured.")
    if config.ai_provider != "nvidia_nim":
        return UnconfiguredAIGateway(
            f"AI provider {config.ai_provider} is not supported by this deployment."
        )

    provider = NvidiaNimProvider(
        api_key=api_key,
        base_url=config.ai_provider_base_url,
        timeout_seconds=config.ai_timeout_seconds,
    )
    recorder = SQLAlchemyAIInvocationRecorder(session) if session is not None else None
    return ProviderAIGateway(
        providers={provider.name: provider},
        registry=model_registry_from_settings(config),
        recorder=recorder,
        max_retries=config.ai_max_retries,
        default_max_output_tokens=config.ai_max_output_tokens,
    )
