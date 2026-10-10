"""Ordered text fallback before a work item exists; vision and execution stay separate."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import cast

from app.application.services.ai_gateway import ProviderAIGateway
from app.domain.ai.providers import AIProviderError

logger = logging.getLogger("uvicorn.error")


class OrderedTextGateway:
    def __init__(
        self, *, legacy, routes, factory, budget_seconds, attempt_seconds: float = 250, clock=time.monotonic,
        workload="interactive", purpose="human_request", workload_factory=None, observer=None
    ):
        self.legacy, self.routes, self.factory = legacy, tuple(routes), factory
        self.budget_seconds = budget_seconds
        self.attempt_seconds = attempt_seconds
        self.clock = clock
        # Interpretation and reply share an interactive deadline. Preparation switches
        # to a cached complex gateway with its own deadline and selected list.
        self.deadline = clock() + budget_seconds
        self.workload, self.purpose = workload, purpose
        self.workload_factory, self.observer = workload_factory, observer
        self._complex_gateway = None

    def with_workload(self, workload, purpose):
        if workload == self.workload:
            self.purpose = purpose
            return self
        if self.workload_factory is None:
            raise AIProviderError("configuration_missing", "Workload routing is not configured.", retryable=False)
        if self._complex_gateway is None:
            self._complex_gateway = self.workload_factory(workload, purpose)
        self._complex_gateway.purpose = purpose
        return self._complex_gateway

    async def _generate(self, method, kwargs):
        role = kwargs["role"]
        if role not in {"intent", "conversation"}:
            return await getattr(self.legacy, method)(**kwargs)
        started = self.clock()
        blocked_accounts = set()
        last_error = None
        failure_categories = []
        attempted = 0
        context = kwargs.get("context")
        logger.info("AI workload started workload=%s purpose=%s role=%s remaining_seconds=%.1f",
                    self.workload, self.purpose, role, self.deadline - self.clock())
        for index, route in enumerate(self.routes):
            account = route["provider"]
            if account in blocked_accounts:
                logger.info(
                    "AI route skipped workload=%s purpose=%s role=%s model=%s connection=%s reason=account_blocked",
                    self.workload, self.purpose, role,
                    route["model"],
                    account,
                )
                continue
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                logger.warning(
                    "AI request deadline expired workload=%s purpose=%s role=%s attempted=%s untried=%s",
                    self.workload, self.purpose, role,
                    attempted,
                    len(self.routes) - index,
                )
                raise AIProviderError(
                    "timeout",
                    "The request deadline expired before all models could be tried.",
                    retryable=False,
                )
            logger.info(
                "AI selection started workload=%s purpose=%s role=%s model=%s connection=%s route=%s correlation=%s attempt_limit_seconds=%.1f remaining_seconds=%.1f",
                self.workload, self.purpose, role,
                route["model"],
                account,
                index,
                getattr(context, "correlation_id", None),
                min(self.attempt_seconds, remaining),
                remaining,
            )
            gateway = None
            attempt_limit = min(self.attempt_seconds, remaining)
            if self.observer and not await self.observer.begin(index, route):
                continue
            try:
                async with asyncio.timeout(attempt_limit):
                    gateway = self.factory(route, role, context)
                    if isinstance(gateway, ProviderAIGateway):
                        gateway.set_cancellation_deadline(
                            datetime.now(UTC) + timedelta(seconds=attempt_limit)
                        )
                    attempted += 1
                    # Interactive text is returned as a complete response, never streamed to UI.
                    if method == "generate_text":
                        kwargs = {**kwargs, "stream": False}
                    result = await getattr(gateway, method)(**kwargs)
                if method == "generate_text" and not result.text.strip():
                    raise AIProviderError(
                        "invalid_provider_response",
                        "Model returned an empty response.",
                        retryable=False,
                        rejection_reason="empty_output",
                    )
                if self.observer:
                    await self.observer.accept(result)
                logger.info(
                    "AI selection accepted workload=%s purpose=%s role=%s model=%s connection=%s elapsed_seconds=%.3f",
                    self.workload, self.purpose, role,
                    route["model"],
                    account,
                    self.clock() - started,
                )
                return result
            except AIProviderError as exc:
                if gateway is None and exc.model_called is None:
                    exc.model_called = False
                if self.observer:
                    await self.observer.reject(exc)
                last_error = exc
                failure_categories.append(exc.category)
                logger.warning(
                    "AI selection rejected workload=%s purpose=%s role=%s model=%s connection=%s category=%s http_status=%s account_scoped=%s organization_scoped=%s reason=%s model_called=%s budget=%s validation_rule=%s origin=%s",
                    self.workload, self.purpose, role,
                    route["model"],
                    account,
                    exc.category,
                    exc.status_code,
                    exc.account_scoped,
                    exc.organization_scoped,
                    exc.rejection_reason,
                    exc.model_called,
                    exc.budget_scope,
                    exc.validation_rule,
                    "local_validation" if exc.category == "invalid_provider_response" else "admission_or_provider",
                )
                if exc.organization_scoped or exc.category in {"content_rejected", "cancelled"}:
                    raise
                if exc.account_scoped or exc.category in {"authentication_failed", "quota_exhausted"}:
                    blocked_accounts.add(account)
            except TimeoutError as exc:
                failure_categories.append("timeout")
                remaining = self.deadline - self.clock()
                if self.observer:
                    await self.observer.reject(AIProviderError("timeout", "Attempt deadline expired.", retryable=remaining > 0))
                if remaining > 0:
                    last_error = AIProviderError(
                        "timeout",
                        "Interactive model attempt exceeded its time allowance.",
                        retryable=True,
                    )
                    logger.warning(
                        "AI attempt timed out role=%s model=%s connection=%s limit_seconds=%.1f remaining_seconds=%.1f; switching to next eligible model",
                        role,
                        route["model"],
                        account,
                        attempt_limit,
                        remaining,
                    )
                    continue
                logger.warning(
                    "AI request deadline expired workload=%s purpose=%s role=%s attempted=%s untried=%s",
                    self.workload, self.purpose, role,
                    attempted,
                    len(self.routes) - index - 1,
                )
                raise AIProviderError(
                    "timeout",
                    "The request deadline expired; remaining models were not tried.",
                    retryable=False,
                ) from exc
            finally:
                close = getattr(gateway, "close", None)
                if callable(close):
                    with suppress(Exception):
                        await asyncio.wait_for(
                            cast(Callable[[], Awaitable[None]], close)(),
                            timeout=min(2, max(0.01, self.deadline - self.clock())),
                        )
        logger.error(
            "AI selection exhausted workload=%s purpose=%s role=%s attempted=%s configured=%s category=%s validation_failures=%s",
            self.workload, self.purpose,
            role,
            attempted,
            len(self.routes),
            last_error.category if last_error else "configuration_missing",
            failure_categories.count("invalid_provider_response"),
        )
        if last_error:
            last_error.validation_exhausted = bool(failure_categories) and all(
                category == "invalid_provider_response" for category in failure_categories)
            raise last_error
        raise AIProviderError(
            "configuration_missing", "No eligible text models are configured.", retryable=False
        )

    async def generate_text(self, **kwargs):
        return await self._generate("generate_text", kwargs)

    async def generate_structured(self, **kwargs):
        return await self._generate("generate_structured", kwargs)

    async def generate_reviewed_structured(self, **kwargs):
        return await self._generate("generate_reviewed_structured", kwargs)

    async def generate_validated_structured(self, **kwargs):
        return await self._generate("generate_validated_structured", kwargs)

    async def analyze_media(self, **kwargs):
        return await self.legacy.analyze_media(**kwargs)
