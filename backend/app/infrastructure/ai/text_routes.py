"""Configured model transport and shared admission for first-request text inference."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import cast

from app.application.services.ai_gateway import ProviderAIGateway
from app.domain.ai.providers import AIProviderError
from app.domain.ai.registry import ModelRegistry, ModelRoute
from app.infrastructure.ai.connections import (
    TextConnection,
    route_account_name,
    route_credential,
    snapshot_connection,
    transport_identity,
)
from app.infrastructure.ai.model_profiles import DEFAULT_PROFILES, generation_allowance
from app.infrastructure.ai.nvidia import NvidiaNimProvider
from app.infrastructure.ai.openai_chat import ConfiguredChatProvider
from app.infrastructure.ai.openrouter import OpenRouterPlannerProvider
from app.infrastructure.redis.coordination import RedisCoordinator

logger = logging.getLogger("uvicorn.error")


def text_routes(config, workload=None):
    routes = []
    selected = (config.ai_interactive_routes if workload == "interactive" else
                config.ai_complex_routes if workload == "complex" else config.smart_planner_routes)
    for raw in selected:
        provider, _, model = raw.partition(":")
        model = config.ai_coordinator_model if model == "configured" else model
        route = {"provider": provider, "model": model}
        if route not in routes:
            routes.append(route)
    return routes


def snapshot_routes(config, workload):
    routes = text_routes(config, workload)
    for route in routes:
        key = f"{route['provider']}:{route['model']}"
        profile = config.smart_planner_model_profiles.get(key) or DEFAULT_PROFILES.get(key)
        if profile:
            route["profile"] = profile.model_dump()
        snapshot_connection(route, config)
    return routes


class AdmittedTextProvider:
    def __init__(self, provider, config, context, credential, profile, workload="interactive"):
        self.provider, self.config, self.context = provider, config, context
        self.profile = profile
        self.name, self.capabilities = provider.name, provider.capabilities
        from app.infrastructure.ai.workloads import account_identity
        self.account = account_identity(config, getattr(provider, "account_name", self.name), credential)
        self.coordinator = None
        self.workload = workload

    async def generate_text(self, *, model, request):
        self.dispatch_started = False
        try:
            return await self._generate_text(model=model, request=request)
        except AIProviderError as error:
            if error.model_called is None:
                error.model_called = self.dispatch_started
            logger.warning("AI admission outcome workload=%s connection=%s model=%s model_called=%s category=%s budget=%s",
                self.workload, self.name, model, error.model_called or False, error.category,
                error.budget_scope or "provider_or_account")
            raise
        except Exception as exc:
            # A shared admission outage cannot be repaired by cycling model accounts.
            raise AIProviderError(
                "configuration_missing",
                "Request coordination is unavailable. No model fallback can repair this shared blocker.",
                retryable=False,
                organization_scoped=True,
                model_called=self.dispatch_started,
            ) from exc

    async def _generate_text(self, *, model, request):
        self.provider.validate_request(model=model, request=request)
        coordinator = self.coordinator = self.coordinator or RedisCoordinator.from_settings()
        health = self.account.replace("planner:account:", "planner:health:") + ":" + model
        if await coordinator.cache_get(self.account):
            raise AIProviderError(
                "rate_limited",
                "This model account is cooling down.",
                retryable=True,
                account_scoped=True,
            )
        if await coordinator.cache_get(health):
            raise AIProviderError(
                "provider_unavailable", "This model is cooling down.", retryable=True
            )
        if isinstance(self.provider, OpenRouterPlannerProvider):
            catalog = await self.provider.readiness(coordinator)
            if not catalog.get(model, {}).get("compatible"):
                raise AIProviderError(
                    "model_not_found",
                    "This text endpoint is unavailable or incompatible.",
                    retryable=False,
                )
        org = getattr(self.context, "organization_id", None)
        slot = None
        probe = None
        reservation, called, usage = None, False, None
        try:
            if int(await coordinator.cache_get(health + ":failures") or 0) >= 5:
                probe = await coordinator.acquire_lock(
                    health + ":probe", ttl_seconds=self.config.runtime_planner_timeout_seconds + 10
                )
                if not probe:
                    raise AIProviderError(
                        "provider_unavailable",
                        "A recovery probe is already running.",
                        retryable=True,
                    )
            if org:
                from app.infrastructure.ai.workloads import acquire_workload_slot
                slot = await acquire_workload_slot(coordinator, self.config, org, self.workload,
                                                 self.config.runtime_planner_timeout_seconds + 10)
                if not slot:
                    raise AIProviderError(
                        "rate_limited",
                        "Organization request concurrency is full.",
                        retryable=True,
                        organization_scoped=True,
                    )
                from app.infrastructure.ai.workloads import reserve_workload_budgets
                bound = len((request.system + request.prompt).encode()) + generation_allowance(
                    self.profile, request.max_output_tokens)
                reservation = await reserve_workload_budgets(coordinator, self.config, org, self.workload, bound)
            from app.infrastructure.ai.workloads import acquire_account_slot, release_workload_slot
            account_slot = await acquire_account_slot(coordinator, self.config, self.account, self.workload,
                self.config.runtime_planner_timeout_seconds + 10)
            if account_slot is None:
                raise AIProviderError("rate_limited", "The model account is at its concurrency limit.",
                                      retryable=True, account_scoped=True, retry_after_seconds=15)
            try:
                called = True
                self.dispatch_started = True
                logger.info("AI model dispatch workload=%s connection=%s model=%s model_called=true",
                    self.workload, self.name, model)
                response = await self.provider.generate_text(model=model, request=request)
                usage = response.usage
            except AIProviderError as exc:
                exc.model_called = called
                if exc.account_scoped or exc.category in {"authentication_failed", "quota_exhausted"}:
                    await coordinator.cache_set(
                        self.account, exc.category, ttl_seconds=exc.retry_after_seconds or 120
                    )
                elif exc.category == "rate_limited":
                    await coordinator.cache_set(
                        health, exc.category, ttl_seconds=exc.retry_after_seconds or 60
                    )
                elif exc.category in {"provider_unavailable", "timeout"}:
                    failures = await coordinator.increment_counter(
                        health + ":failures", ttl_seconds=300
                    )
                    if failures >= 5:
                        await coordinator.cache_set(health, exc.category, ttl_seconds=120)
                raise
            finally:
                await release_workload_slot(coordinator, account_slot)
            await coordinator.cache_delete(health + ":failures")
            return response
        finally:
            from app.infrastructure.ai.workloads import settle_workload_budget
            await settle_workload_budget(coordinator, reservation, called=called, usage=usage)
            if slot:
                from app.infrastructure.ai.workloads import release_workload_slot
                await release_workload_slot(coordinator, slot)
            if probe:
                await coordinator.release_lock(probe)

    async def close(self):
        if self.coordinator:
            with suppress(Exception):
                await self.coordinator.close()

    async def analyze_media(self, **kwargs):
        raise AIProviderError(
            "configuration_missing", "Text fallback cannot route vision.", retryable=False
        )


class TextRouteGateway(ProviderAIGateway):
    async def close(self):
        for provider in self._providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                await cast(Callable[[], Awaitable[None]], close)()


def transport_for_text_route(route, config, *, timeout_seconds=None):
    name, model = route["provider"], route["model"]
    profile = route.get("profile") or config.smart_planner_model_profiles.get(f"{name}:{model}") or DEFAULT_PROFILES.get(
        f"{name}:{model}"
    )
    if isinstance(profile, dict):
        from app.infrastructure.ai.model_profiles import PlannerModelProfile
        profile = PlannerModelProfile.model_validate(profile)
    if profile is None:
        raise AIProviderError(
            "configuration_missing", "Model compatibility profile is missing.", retryable=False
        )
    credential = route_credential(route, config)
    if name == "nvidia_nim":
        provider = NvidiaNimProvider(
            coordinator_api_key=credential,
            base_url=route.get("endpoint", "").removesuffix("/chat/completions") or config.ai_provider_base_url,
            timeout_seconds=timeout_seconds or config.ai_interactive_timeout_seconds,
            planner_profile=profile,
        )
    elif name == "openrouter":
        provider = OpenRouterPlannerProvider(
            api_key=credential,
            allowed_providers=config.openrouter_allowed_providers,
            data_allowed=config.openrouter_planner_data_allowed,
            timeout_seconds=timeout_seconds or config.ai_interactive_timeout_seconds,
            models=[model],
            profiles={model: profile},
        )
    else:
        connection = route.get("connection") or getattr(config, "ai_text_connections", {}).get(name)
        if not connection:
            raise AIProviderError("configuration_missing", "Model transport is unsupported.", retryable=False)
        current = getattr(config, "ai_text_connections", {}).get(name)
        if not current or not current.enabled or not current.data_allowed:
            raise AIProviderError("configuration_missing", "Text connection is disabled or data handling is not authorized.", retryable=False)
        connection = TextConnection.model_validate(connection).model_copy(update={
            "enabled": current.enabled, "data_allowed": current.data_allowed,
            "billing_allowed": current.billing_allowed,
            "billing_required": current.billing_required or TextConnection.model_validate(connection).billing_required,
        })
        provider = ConfiguredChatProvider(name=name, connection=TextConnection.model_validate(connection),
            credential=credential, profile=profile,
            timeout_seconds=timeout_seconds or config.ai_interactive_timeout_seconds)
        provider.account_name = route_account_name(route, config)
    return provider, credential, profile


def text_gateway(route, role, context, config, recorder, workload="interactive"):
    name, model = route["provider"], route["model"]
    provider, credential, profile = transport_for_text_route(
        route, config, timeout_seconds=(config.runtime_planner_timeout_seconds
                                      if workload == "complex" else config.ai_interactive_timeout_seconds))
    admitted = AdmittedTextProvider(provider, config, context, credential, profile, workload)
    if route.get("endpoint"):
        from app.infrastructure.ai.telemetry import TransportInvocationRecorder
        recorder = TransportInvocationRecorder(recorder, transport_identity(route)) if recorder else None
    return TextRouteGateway(
        providers={name: admitted},
        registry=ModelRegistry([ModelRoute(role=role, provider=name, model=model)]),
        recorder=recorder,
        max_retries=0,
        default_max_output_tokens=config.ai_max_output_tokens,
    )
