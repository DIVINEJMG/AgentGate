"""Text-only configured chat transport with no provider/model fallback or retries."""

import logging

import httpx

from app.domain.ai.providers import AIProviderError, AIResponse, ModelProviderCapabilities
from app.infrastructure.ai.connections import wire_model
from app.infrastructure.ai.model_profiles import apply_profile, validate_context
from app.infrastructure.ai.nvidia import (
    _content_from_payload,
    _error_category,
    _retry_after,
    _usage_from_payload,
)
from app.infrastructure.ai.streaming import collect_response

logger = logging.getLogger("uvicorn.error")


def groq_strict_compatible(schema):
    """Conservative supported subset; never rewrite optional/open action payloads."""
    if not isinstance(schema, dict):
        return False
    if set(schema) - {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "anyOf",
        "description",
        "title",
    }:
        return False
    kind = schema.get("type")
    if isinstance(kind, list):
        if not kind or any(
            t not in {"string", "number", "integer", "boolean", "null"} for t in kind
        ):
            return False
    elif kind == "object":
        properties = schema.get("properties", {})
        if (
            schema.get("additionalProperties") is not False
            or not isinstance(properties, dict)
            or set(schema.get("required", [])) != set(properties)
        ):
            return False
        return all(groq_strict_compatible(item) for item in properties.values())
    elif kind == "array":
        return groq_strict_compatible(schema.get("items"))
    elif kind not in {"string", "number", "integer", "boolean", "null"}:
        alternatives = schema.get("anyOf")
        return (
            isinstance(alternatives, list)
            and bool(alternatives)
            and all(groq_strict_compatible(s) for s in alternatives)
        )
    return True


class ConfiguredChatProvider:
    capabilities = ModelProviderCapabilities(text_input=True, structured_json=True, streaming=True)

    def __init__(self, *, name, connection, credential, profile, timeout_seconds, transport=None):
        self.name, self.connection, self.credential = name, connection, credential
        self.account_name = name
        self.planner_profile, self.timeout_seconds, self.transport = (
            profile,
            timeout_seconds,
            transport,
        )
        self.planner_progress = None

    def validate_request(self, *, model, request):
        if not self.connection.enabled or not self.connection.data_allowed:
            raise AIProviderError(
                "configuration_missing",
                "Text connection is disabled or data handling is not authorized.",
                retryable=False,
            )
        if self.connection.billing_required and not self.connection.billing_allowed:
            raise AIProviderError(
                "configuration_missing",
                "This endpoint requires explicit billing authorization.",
                retryable=False,
            )
        if not self.credential:
            raise AIProviderError(
                "configuration_missing",
                "Text connection credential is missing.",
                retryable=False,
                account_scoped=True,
            )
        wire_model(self.connection, model)
        validate_context(
            self.planner_profile, request.system, request.prompt, request.max_output_tokens
        )

    def body(self, model, request):
        body = {
            "model": wire_model(self.connection, model),
            "messages": ([{"role": "system", "content": request.system}] if request.system else [])
            + [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
            "stream": request.stream,
        }
        apply_profile(body, self.planner_profile, request)
        # The endpoint, not a model's generic JSON preference, constrains the wire format.
        body.pop("response_format", None)
        mode = self.connection.schema_mode
        if request.response_format == "json_object":
            if mode in {"json_object", "groq_strict", "json_schema"}:
                body["response_format"] = {"type": "json_object"}
            if request.json_schema and (
                mode == "json_schema"
                or mode == "groq_strict"
                and groq_strict_compatible(request.json_schema)
            ):
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name or "decision",
                        "schema": request.json_schema,
                        "strict": True,
                    },
                }
        if mode == "groq_strict" and body.get("response_format", {}).get("type") == "json_schema":
            # Groq documents that native Structured Outputs do not support streaming.
            body["stream"] = False
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        return body

    async def generate_text(self, *, model, request):
        self.validate_request(model=model, request=request)
        requested = wire_model(self.connection, model)
        endpoint = self.connection.base_url + "/chat/completions"
        body = self.body(model, request)
        logger.info(
            "AI transport started connection=%s endpoint=%s hosting_provider=%s requested_model=%s",
            self.name,
            endpoint,
            self.connection.hosting_provider,
            requested,
        )
        try:
            async with (
                httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                ) as client,
                client.stream(
                    "POST",
                    endpoint,
                    headers={"Authorization": f"Bearer {self.credential}"},
                    json=body,
                ) as response,
            ):
                if response.status_code >= 300:
                    # Bodies may echo tenant content or credentials; classification uses status only.
                    category, retryable = _error_category(response.status_code, "")
                    if response.status_code == 402:
                        category, retryable = "quota_exhausted", False
                    raise AIProviderError(
                        category,
                        f"Text endpoint returned HTTP {response.status_code}.",
                        retryable=retryable,
                        status_code=response.status_code,
                        account_scoped=response.status_code in {401, 403, 402, 429},
                        retry_after_seconds=_retry_after(response.headers.get("Retry-After")),
                    )
                if body["stream"]:
                    payload = await collect_response(
                        response,
                        model=requested,
                        accepted_models={model, requested},
                        progress=self.planner_progress,
                    )
                else:
                    await response.aread()
                    payload = response.json()
                if not isinstance(payload, dict) or payload.get("model") not in {
                    model,
                    requested,
                }:
                    raise AIProviderError(
                        "invalid_provider_response",
                        "Unexpected response model identity.",
                        retryable=False,
                        rejection_reason="model_mismatch",
                    )
                choices = payload.get("choices")
                first = choices[0] if isinstance(choices, list) and choices else {}
                if not isinstance(first, dict):
                    raise TypeError("Invalid choices")
                message = first.get("message") or {}
                if not isinstance(message, dict):
                    raise TypeError("Invalid message")
                if message.get("refusal") or first.get("finish_reason") == "content_filter":
                    raise AIProviderError(
                        "content_rejected", "Model declined the request.", retryable=False
                    )
                text = _content_from_payload(payload)
                if first.get("finish_reason") != "stop" or not text:
                    raise AIProviderError(
                        "invalid_provider_response",
                        "Text response was empty or incomplete.",
                        retryable=False,
                        rejection_reason=("truncated_output" if first.get("finish_reason") == "length"
                            else "empty_output" if not text else "incomplete_output"),
                    )
                logger.info(
                    "AI transport received connection=%s hosting_provider=%s returned_model=%s",
                    self.name,
                    self.connection.hosting_provider,
                    payload["model"],
                )
                return AIResponse(
                    text=text,
                    provider=self.name,
                    model=model,
                    usage=_usage_from_payload(payload),
                    request_id=response.headers.get("x-request-id") or payload.get("id"),
                )
        except AIProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise AIProviderError("timeout", "Text transport timed out.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(
                "provider_unavailable", "Text endpoint could not be reached.", retryable=True
            ) from exc
        except (ValueError, TypeError) as exc:
            raise AIProviderError(
                "invalid_provider_response", "Unreadable text response.", retryable=False,
                rejection_reason="invalid_envelope",
            ) from exc

    async def analyze_media(self, **kwargs):
        raise AIProviderError(
            "configuration_missing",
            "Configured text connections cannot route vision.",
            retryable=False,
        )
