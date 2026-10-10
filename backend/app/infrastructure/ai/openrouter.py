from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable

import httpx

from app.domain.ai.providers import (
    AIProviderError,
    AIResponse,
    AITextRequest,
    ModelProviderCapabilities,
)
from app.infrastructure.ai.model_profiles import (
    DEFAULT_PROFILES,
    PlannerModelProfile,
    apply_profile,
    model_profile,
    validate_context,
)

# Endpoint metadata is refreshed by readiness checks; local validation always remains mandatory.
MODEL_FORMATS = {key.partition(":")[2]: (None if p.output_format == "prompt_json" else "json_object")
    for key, p in DEFAULT_PROFILES.items() if key.startswith("openrouter:")}
MODEL_CONTEXT = {key.partition(":")[2]: p.context_tokens
    for key, p in DEFAULT_PROFILES.items() if key.startswith("openrouter:")}


class OpenRouterPlannerProvider:
    name = "openrouter"
    capabilities = ModelProviderCapabilities(text_input=True, structured_json=True, streaming=True)

    def __init__(
        self,
        *,
        api_key: str | None,
        allowed_providers: list[str],
        data_allowed: bool,
        timeout_seconds: int = 180,
        transport=None,
        models: list[str] | None = None,
        profiles: dict[str, PlannerModelProfile] | None = None,
    ):
        self.api_key = api_key
        self.allowed_providers = allowed_providers
        self.data_allowed = data_allowed
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.models = models or list(MODEL_FORMATS)
        self.profiles = profiles or {}
        self.planner_progress: Callable[[int], Awaitable[None]] | None = None

    def profile_for(self, model: str) -> PlannerModelProfile:
        return self.profiles.get(model) or model_profile(self.name, model)

    def validate_request(self, *, model: str, request: AITextRequest) -> None:
        if any("," in tag or ":" in tag for tag in self.allowed_providers):
            raise AIProviderError("configuration_missing", "Use hosting identifiers rather than model IDs in the allowed provider list.", retryable=False, account_scoped=True)
        if not self.api_key or not self.data_allowed or not self.allowed_providers:
            raise AIProviderError(
                "configuration_missing",
                "Planner endpoint credentials and data-handling restrictions are required.",
                retryable=False,
                account_scoped=True,
            )
        if not model.endswith(":free"):
            raise AIProviderError("configuration_missing", "Paid planner models require separate authorization.", retryable=False)
        profile = self.profile_for(model)
        validate_context(profile, request.system, request.prompt, request.max_output_tokens)

    async def generate_text(self, *, model: str, request: AITextRequest) -> AIResponse:
        self.validate_request(model=model, request=request)
        profile = self.profile_for(model)
        # A single model is requested. Provider fallback may only use explicitly allowed endpoints.
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": request.stream,
            "provider": {
                "only": self.allowed_providers,
                "data_collection": "deny",
                "allow_fallbacks": False,
            },
        }
        apply_profile(body, profile, request)
        if "response_format" in body:
            body["provider"]["require_parameters"] = True
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                if request.stream:
                    from app.infrastructure.ai.streaming import collect_response
                    async with client.stream(
                        "POST", "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"}, json=body,
                    ) as response:
                        if response.status_code >= 400:
                            await response.aread()
                            try:
                                payload = response.json()
                            except ValueError:
                                payload = {}
                        else:
                            payload = await collect_response(response, model=model,
                                progress=getattr(self, "planner_progress", None))
                else:
                    response = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=body,
                    )
                    try:
                        payload = response.json()
                    except ValueError:
                        if response.status_code < 400:
                            raise
                        payload = {}
            if response.status_code >= 400:
                status = response.status_code
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                metadata = error.get("metadata", {}) if isinstance(error, dict) else {}
                account = status in {401, 402} or (
                    status == 429
                    and not (metadata.get("provider_code") or metadata.get("provider_name"))
                )
                category = (
                    "authentication_failed"
                    if status == 401
                    else "quota_exhausted"
                    if status == 402
                    else "rate_limited"
                    if status == 429
                    else "model_not_found"
                    if status == 404
                    else "content_rejected"
                    if status == 403 and ("safety" in str(error.get("message", "")).lower()
                        or "content filter" in str(error.get("message", "")).lower())
                    else "configuration_missing"
                    if status == 403
                    else "provider_unavailable"
                    if status >= 500
                    else "invalid_provider_response"
                )
                retry = response.headers.get("Retry-After", "")
                raise AIProviderError(
                    category,
                    "Planner endpoint rejected the request.",
                    retryable=status == 429 or status >= 500,
                    status_code=status,
                    account_scoped=account,
                    retry_after_seconds=int(retry) if retry.isdigit() else None,
                )
            choice = payload["choices"][0]
            if choice.get("finish_reason") in {"length", "error", "content_filter"}:
                raise AIProviderError(
                    "content_rejected"
                    if choice["finish_reason"] == "content_filter"
                    else "invalid_provider_response",
                    "Planner response was incomplete or refused.",
                    retryable=False,
                    rejection_reason="truncated_output" if choice["finish_reason"] == "length" else "incomplete_output",
                )
            if choice["message"].get("refusal"):
                raise AIProviderError("content_rejected", "Planner declined this request.", retryable=False)
            text = choice["message"].get("content")
            actual = payload.get("model", model)
            if actual != model or not isinstance(text, str) or not text.strip():
                raise AIProviderError(
                    "invalid_provider_response",
                    "Planner returned an unexpected model or empty response.",
                    retryable=False,
                    rejection_reason="model_mismatch" if actual != model else "empty_output",
                )
            usage = {k: v for k, v in payload.get("usage", {}).items() if isinstance(v, int)}
            return AIResponse(
                text=text,
                provider=self.name,
                model=actual,
                request_id=payload.get("id"),
                usage=usage,
            )
        except httpx.TimeoutException as exc:
            raise AIProviderError("timeout", "Planner request timed out.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(
                "provider_unavailable", "Planner endpoint could not be reached.", retryable=True
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderError(
                "invalid_provider_response",
                "Planner returned an unreadable response.",
                retryable=False,
                rejection_reason="invalid_envelope",
            ) from exc

    async def readiness(self, coordinator):
        signature = json.dumps({model: self.profile_for(model).model_dump() for model in self.models}, sort_keys=True)
        cache_key = "planner:openrouter:catalog:" + hashlib.sha256(signature.encode()).hexdigest()[:16]
        cached = await coordinator.cache_get(cache_key)
        if cached:
            catalog = json.loads(cached)
            if all(model in catalog for model in self.models):
                return catalog
        async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
            response = await client.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()
        rows = {row["id"]: row for row in response.json().get("data", [])}
        result = {}
        for model in self.models:
            profile = self.profile_for(model)
            output_format = None if profile.output_format == "prompt_json" else profile.output_format
            row = rows.get(model)
            result[model] = {
                "available": bool(row),
                "format": output_format,
                "compatible": bool(row)
                and (not output_format or "response_format" in row.get("supported_parameters", [])),
                "contextTokens": row.get("context_length") if row else None,
                "reason": None if row else "model_unavailable",
            }
        await coordinator.cache_set(
            cache_key, json.dumps(result), ttl_seconds=300
        )
        return result

    async def analyze_media(self, **kwargs):
        raise AIProviderError(
            "configuration_missing", "This endpoint is planner-only.", retryable=False
        )
