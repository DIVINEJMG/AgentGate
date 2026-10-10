from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, NoReturn, cast

from jsonschema import Draft202012Validator

from app.domain.ai.providers import (
    AIGateway,
    AIInvocationContext,
    AIInvocationRecorder,
    AIMediaInput,
    AIModelProvider,
    AIModelRole,
    AIProviderError,
    AIResponse,
    AITextRequest,
)
from app.domain.ai.registry import ModelRegistry

logger = logging.getLogger("uvicorn.error")


async def observe_inference(operation, *, role, model, provider, context, heartbeat_seconds: float = 30):
    started = time.monotonic()
    correlation = context.correlation_id
    logger.info("AI inference started role=%s model=%s connection=%s correlation=%s", role, model, provider, correlation)

    async def heartbeat():
        while True:
            await asyncio.sleep(heartbeat_seconds)
            logger.info("AI inference waiting role=%s model=%s connection=%s correlation=%s elapsed_seconds=%.1f", role, model, provider, correlation, time.monotonic() - started)

    pulse = asyncio.create_task(heartbeat())
    outcome = "interrupted"
    try:
        result = await operation()
        outcome = "response_received"
        return result
    except AIProviderError as exc:
        outcome = exc.category
        logger.warning("AI inference failed role=%s model=%s connection=%s correlation=%s category=%s http_status=%s reason=%s", role, model, provider, correlation, exc.category, exc.status_code, exc.rejection_reason)
        raise
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse
        logger.info("AI inference ended role=%s model=%s connection=%s correlation=%s outcome=%s elapsed_seconds=%.3f", role, model, provider, correlation, outcome, time.monotonic() - started)


def _parse_json_object(text: str) -> dict[str, object]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIProviderError(
            "invalid_provider_response",
            "AI provider returned invalid JSON.",
            retryable=False,
            rejection_reason="malformed_json" if raw else "empty_output",
        ) from exc
    if not isinstance(parsed, dict):
        raise AIProviderError(
            "invalid_provider_response",
            "AI provider returned a non-object structured response.",
            retryable=False,
            rejection_reason="schema_failure",
        )
    return {str(key): value for key, value in parsed.items()}


class UnconfiguredAIGateway(AIGateway):
    def __init__(self, message: str = "Aduoryn AI is not configured.") -> None:
        self._message = message

    def _raise(self) -> NoReturn:
        raise AIProviderError(
            "configuration_missing",
            self._message,
            retryable=False,
        )

    async def generate_text(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
        stream: bool = False,
    ) -> AIResponse:
        del role, system, prompt, context, max_output_tokens, temperature, stream
        self._raise()

    async def generate_structured(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, object],
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> dict[str, object]:
        del role, system, prompt, schema_name, schema, context, max_output_tokens, temperature
        self._raise()

    async def analyze_media(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        media: AIMediaInput,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
    ) -> AIResponse:
        del role, system, prompt, media, context, max_output_tokens
        self._raise()


class ProviderAIGateway(AIGateway):
    def __init__(
        self,
        *,
        providers: dict[str, AIModelProvider],
        registry: ModelRegistry,
        recorder: AIInvocationRecorder | None = None,
        max_retries: int = 2,
        default_max_output_tokens: int = 1800,
        cancellation_deadline: datetime | None = None,
    ) -> None:
        self._providers = providers
        self._registry = registry
        self._recorder = recorder
        self._max_retries = max(0, max_retries)
        self._default_max_output_tokens = max(16, default_max_output_tokens)
        self._cancellation_deadline = cancellation_deadline

    def set_cancellation_deadline(self, deadline: datetime) -> None:
        self._cancellation_deadline = deadline

    def _provider_for(self, role: AIModelRole) -> tuple[AIModelProvider, str]:
        route = self._registry.route(role)
        try:
            provider = self._providers[route.provider]
        except KeyError as exc:
            raise AIProviderError(
                "configuration_missing",
                f"AI provider {route.provider} is not registered.",
                retryable=False,
            ) from exc
        return provider, route.model

    async def _invoke(
        self,
        *,
        role: AIModelRole,
        request: AITextRequest,
        context: AIInvocationContext,
        schema_name: str | None,
        operation: Callable[[AIModelProvider, str, AITextRequest], Awaitable[AIResponse]],
        max_retries: int | None = None,
    ) -> AIResponse:
        provider, model = self._provider_for(role)
        invocation_id = None
        if self._recorder is not None:
            invocation_id = await self._recorder.start(
                context=context,
                role=role,
                provider=provider.name,
                model=model,
                schema_name=schema_name,
            )

        started = time.monotonic()
        last_error: AIProviderError | None = None
        retry_limit = self._max_retries if max_retries is None else max(0, max_retries)
        for attempt in range(retry_limit + 1):
            try:
                response = await observe_inference(
                    lambda: operation(provider, model, request), role=role, model=model,
                    provider=provider.name, context=context,
                )
                if invocation_id is not None and self._recorder is not None:
                    await self._recorder.finish(
                        invocation_id,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        success=True,
                        request_id=response.request_id,
                        usage=response.usage,
                        error_category=None,
                    )
                return response
            except asyncio.CancelledError:
                # asyncio.timeout cancels the invocation before raising TimeoutError outside it.
                # Finish telemetry in its own transaction, then preserve cancellation semantics.
                if invocation_id is not None and self._recorder is not None:
                    # Telemetry failure must never swallow task cancellation.
                    with suppress(Exception, asyncio.CancelledError):
                        await asyncio.wait_for(asyncio.shield(self._recorder.finish(
                            invocation_id, latency_ms=int((time.monotonic() - started) * 1000),
                            success=False, request_id=None, usage={}, error_category=(
                                "timeout" if self._cancellation_deadline is not None
                                and datetime.now(UTC) >= self._cancellation_deadline else "cancelled"),
                        )), timeout=5)
                raise
            except AIProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt >= retry_limit:
                    if invocation_id is not None and self._recorder is not None:
                        await self._recorder.finish(
                            invocation_id,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            success=False,
                            request_id=None,
                            usage={},
                            error_category=exc.category,
                        )
                    raise
                await asyncio.sleep(min(2.0, 0.25 * (2**attempt)))
            except Exception as exc:
                wrapped = AIProviderError(
                    "provider_unavailable",
                    "AI provider invocation failed unexpectedly.",
                    retryable=True,
                )
                if invocation_id is not None and self._recorder is not None:
                    await self._recorder.finish(
                        invocation_id,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        success=False,
                        request_id=None,
                        usage={},
                        error_category=wrapped.category,
                    )
                raise wrapped from exc

        assert last_error is not None
        raise last_error

    async def generate_text(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
        stream: bool = False,
    ) -> AIResponse:
        provider, _ = self._provider_for(role)
        if not provider.capabilities.text_input:
            raise AIProviderError(
                "configuration_missing",
                f"AI provider {provider.name} does not support text input.",
                retryable=False,
            )
        invocation_context = context or AIInvocationContext()
        request = AITextRequest(
            system=system,
            prompt=prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens or self._default_max_output_tokens,
            stream=stream,
            correlation_id=invocation_context.correlation_id,
        )

        async def operation(
            selected: AIModelProvider,
            model: str,
            current: AITextRequest,
        ) -> AIResponse:
            return await selected.generate_text(model=model, request=current)

        return await self._invoke(
            role=role,
            request=request,
            context=invocation_context,
            schema_name=None,
            operation=operation,
        )

    async def generate_structured(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, object],
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
        max_inference_calls: int = 2,
    ) -> dict[str, object]:
        provider, _ = self._provider_for(role)
        if not provider.capabilities.structured_json:
            raise AIProviderError(
                "configuration_missing",
                f"AI provider {provider.name} does not support structured JSON.",
                retryable=False,
            )

        schema_json = json.dumps(
            schema,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        base_prompt = (
            prompt
            + "\n\nAUTHORITATIVE_JSON_SCHEMA\n"
            + schema_json
            + "\n\nReturn exactly one JSON object that validates against "
            + schema_name
            + ". Include every required property. Do not add properties that "
            + "the schema forbids. Do not wrap the JSON in markdown."
        )
        feedback = ""
        if max_inference_calls not in {1, 2}:
            raise ValueError("Structured inference is limited to two calls.")
        for repair_attempt in range(max_inference_calls):
            current_prompt = base_prompt
            if feedback:
                current_prompt += (
                    "\n\nYOUR PREVIOUS RESPONSE WAS INVALID. "
                    "Return one corrected JSON object matching the schema exactly.\n"
                    + feedback
                )
            invocation_context = context or AIInvocationContext()
            request = AITextRequest(
                system=system,
                prompt=current_prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens or self._default_max_output_tokens,
                response_format="json_object",
                correlation_id=invocation_context.correlation_id,
                json_schema=schema, schema_name=schema_name,
            )

            async def operation(
                selected: AIModelProvider,
                model: str,
                current: AITextRequest,
            ) -> AIResponse:
                return await selected.generate_text(model=model, request=current)

            response = await self._invoke(
                role=role,
                request=request,
                context=invocation_context,
                schema_name=schema_name,
                operation=operation,
                max_retries=0 if role == "planner" or max_inference_calls == 1 else None,
            )
            try:
                parsed = _parse_json_object(response.text)
            except AIProviderError as exc:
                feedback = str(exc)
                if repair_attempt == max_inference_calls - 1:
                    raise
                continue

            errors = sorted(
                Draft202012Validator(schema).iter_errors(cast(Any, parsed)),
                key=lambda item: list(item.path),
            )
            if not errors:
                return parsed
            feedback = "; ".join(error.message for error in errors[:8])
            if repair_attempt == max_inference_calls - 1:
                raise AIProviderError(
                    "invalid_provider_response",
                    f"AI structured response failed {schema_name} validation: {feedback}",
                    retryable=False,
                    rejection_reason="schema_failure",
                )

        raise AIProviderError(
            "invalid_provider_response",
            f"AI structured response failed {schema_name} validation.",
            retryable=False,
        )

    async def generate_validated_structured(self, *, proposal_validator, **kwargs):
        """Accept valid proposals directly; correct only an actual validation failure."""
        feedback = ""
        correction_instruction = kwargs.pop("correction_instruction", (
            "Repair the proposal for this same schema and stage. Preserve the original objective, "
            "authority, evidence and output contract; do not execute actions or invent outcomes."))
        _, model = self._provider_for(kwargs["role"])
        for call in range(2):
            try:
                proposal = await self.generate_structured(
                    **{**kwargs, "prompt": kwargs["prompt"] + feedback}, max_inference_calls=1)
                proposal_validator(proposal)
                return proposal
            except ValueError as error:
                failure = AIProviderError("invalid_provider_response", str(error), retryable=False,
                    rejection_reason="proposal_validation")
            except AIProviderError as error:
                if error.category != "invalid_provider_response":
                    raise
                failure = error
            failure.model_called = True
            logger.warning("AI proposal rejected role=%s model=%s schema=%s call=%s category=%s reason=%s validation_rule=%s origin=local_validation model_called=%s",
                kwargs["role"], model, kwargs["schema_name"], call + 1, failure.category,
                failure.rejection_reason, failure.validation_rule, failure.model_called)
            if call == 1:
                raise failure
            feedback = ("\n\nCORRECTION_REQUIRED:\nReason: "
                + (failure.rejection_reason or "proposal_validation")
                + "; rule: " + (failure.validation_rule or "contract_validation")
                + "\n" + correction_instruction
                + "\nReturn one corrected object matching the original schema. A rejected response is not evidence.")
        raise AssertionError("Unreachable correction loop")

    async def generate_reviewed_structured(self, *, review_system, review_prompt,
                                          review_schema, review_validator, **kwargs):
        """One proposal plus one semantic review/correction in the same model attempt."""
        _, model = self._provider_for(kwargs["role"])
        logger.info("AI semantic review started role=%s model=%s schema=%s",
                    kwargs["role"], model, kwargs["schema_name"])
        candidate = None
        try:
            candidate = await self.generate_structured(**kwargs, max_inference_calls=1)
        except AIProviderError as error:
            if error.category != "invalid_provider_response":
                raise
        reviewed = await self.generate_structured(
            **{**kwargs, "system": review_system, "prompt": review_prompt(candidate),
               "schema": review_schema, "schema_name": kwargs["schema_name"] + "_review"},
            max_inference_calls=1)
        try:
            review_validator(reviewed)
        except ValueError as error:
            logger.warning("AI semantic review rejected role=%s model=%s schema=%s category=invalid_provider_response",
                           kwargs["role"], model, kwargs["schema_name"])
            raise AIProviderError("invalid_provider_response",
                "Semantic review contradicted the request or authoritative evidence.", retryable=False) from error
        logger.info("AI semantic review accepted role=%s model=%s schema=%s",
                    kwargs["role"], model, kwargs["schema_name"])
        return reviewed

    async def analyze_media(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        media: AIMediaInput,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
    ) -> AIResponse:
        provider, _ = self._provider_for(role)
        if not provider.capabilities.image_input:
            raise AIProviderError(
                "configuration_missing",
                f"AI provider {provider.name} does not support image input.",
                retryable=False,
            )
        invocation_context = context or AIInvocationContext()
        request = AITextRequest(
            system=system,
            prompt=prompt,
            max_output_tokens=max_output_tokens or self._default_max_output_tokens,
            correlation_id=invocation_context.correlation_id,
        )

        async def operation(
            selected: AIModelProvider,
            model: str,
            current: AITextRequest,
        ) -> AIResponse:
            return await selected.analyze_media(
                model=model,
                request=current,
                media=media,
            )

        return await self._invoke(
            role=role,
            request=request,
            context=invocation_context,
            schema_name=None,
            operation=operation,
        )
