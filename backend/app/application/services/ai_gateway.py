from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable

from jsonschema import Draft202012Validator

from app.domain.ai.providers import (
    AIGateway,
    AIErrorCategory,
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
        ) from exc
    if not isinstance(parsed, dict):
        raise AIProviderError(
            "invalid_provider_response",
            "AI provider returned a non-object structured response.",
            retryable=False,
        )
    return {str(key): value for key, value in parsed.items()}


class UnconfiguredAIGateway(AIGateway):
    def __init__(self, message: str = "Aduoryn AI is not configured.") -> None:
        self._message = message

    def _raise(self) -> None:
        raise AIProviderError(
            "configuration_missing",
            self._message,
            retryable=False,
        )

    async def generate_text(self, **_: object) -> AIResponse:
        self._raise()

    async def generate_structured(self, **_: object) -> dict[str, object]:
        self._raise()

    async def analyze_media(self, **_: object) -> AIResponse:
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
    ) -> None:
        self._providers = providers
        self._registry = registry
        self._recorder = recorder
        self._max_retries = max(0, max_retries)
        self._default_max_output_tokens = max(16, default_max_output_tokens)

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
        for attempt in range(self._max_retries + 1):
            try:
                response = await operation(provider, model, request)
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
            except AIProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self._max_retries:
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
    ) -> dict[str, object]:
        provider, _ = self._provider_for(role)
        if not provider.capabilities.structured_json:
            raise AIProviderError(
                "configuration_missing",
                f"AI provider {provider.name} does not support structured JSON.",
                retryable=False,
            )

        feedback = ""
        for repair_attempt in range(2):
            current_prompt = prompt
            if feedback:
                current_prompt += (
                    "\n\nYOUR PREVIOUS RESPONSE WAS INVALID. "
                    "Return one JSON object matching the schema exactly.\n"
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
            )
            try:
                parsed = _parse_json_object(response.text)
            except AIProviderError as exc:
                feedback = str(exc)
                if repair_attempt == 1:
                    raise
                continue

            errors = sorted(
                Draft202012Validator(schema).iter_errors(parsed),
                key=lambda item: list(item.path),
            )
            if not errors:
                return parsed
            feedback = "; ".join(error.message for error in errors[:8])
            if repair_attempt == 1:
                raise AIProviderError(
                    "invalid_provider_response",
                    f"AI structured response failed {schema_name} validation: {feedback}",
                    retryable=False,
                )

        raise AIProviderError(
            "invalid_provider_response",
            f"AI structured response failed {schema_name} validation.",
            retryable=False,
        )

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
