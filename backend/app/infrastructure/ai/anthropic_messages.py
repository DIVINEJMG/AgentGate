"""Claude through the native Messages API.

Anthropic's OpenAI-compatible endpoint ignores the requested JSON format, has no prompt caching
and rejects a temperature below 1 on current models, so Claude gets its own transport:
- structured outputs (`output_config.format`) whenever the schema fits Claude's subset; every
  answer is still validated against the original schema afterwards;
- the system prompt is cached (repeated instructions cost a tenth of the input price);
- effort comes from the model profile (`reasoning_effort`); temperature is left to the model.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx

from app.domain.ai.providers import AIProviderError, AIResponse
from app.infrastructure.ai.connections import wire_model
from app.infrastructure.ai.model_profiles import generation_allowance
from app.infrastructure.ai.nvidia import _error_category, _retry_after
from app.infrastructure.ai.openai_chat import ConfiguredChatProvider
from app.infrastructure.ai.streaming import timeout_error, transport_timeout

logger = logging.getLogger("uvicorn.error")

ANTHROPIC_VERSION = "2023-06-01"
EFFORTS = {"low", "medium", "high", "max"}
# JSON Schema keywords Claude's structured outputs reject. They are dropped from what is sent;
# the original schema still checks the answer.
UNSUPPORTED_KEYWORDS = {
    "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "maxItems", "uniqueItems", "minProperties", "maxProperties", "pattern",
    "patternProperties", "propertyNames", "contains", "if", "then", "else", "not",
    "discriminator", "examples", "prefixItems", "dependentRequired", "dependentSchemas",
}
FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}


class _Unsupported(Exception):
    pass


def _references(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(("#/$defs/", "#/definitions/")):
            found.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            found |= _references(value)
    elif isinstance(node, list):
        for value in node:
            found |= _references(value)
    return found


def _recursive(definitions: dict[str, Any]) -> bool:
    graph = {name: _references(node) for name, node in definitions.items()}
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(name: str) -> bool:
        if name in done:
            return False
        if name in visiting:
            return True
        visiting.add(name)
        cyclic = any(visit(child) for child in graph.get(name, ()) if child in graph)
        visiting.discard(name)
        done.add(name)
        return cyclic

    return any(visit(name) for name in graph)


def _clean(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in UNSUPPORTED_KEYWORDS or (key == "format" and value not in FORMATS):
            continue
        if key == "minItems":
            out[key] = min(int(value), 1) if isinstance(value, int) else 0
        elif key in {"properties", "$defs", "definitions"} and isinstance(value, dict):
            out[key] = {name: _clean(item) for name, item in value.items()}
        else:
            out["anyOf" if key == "oneOf" else key] = _clean(value)
    if "allOf" in out:
        parts = out.pop("allOf")
        if isinstance(parts, list) and len(parts) == 1 and isinstance(parts[0], dict):
            out = {**parts[0], **out}  # Pydantic wraps a described reference this way.
        elif any(isinstance(part, dict) and "$ref" in part for part in parts):
            raise _Unsupported
        else:
            out["allOf"] = parts
    kinds = out.get("type")
    if kinds == "object" or (isinstance(kinds, list) and "object" in kinds) or "properties" in out:
        extra = out.get("additionalProperties")
        if extra not in (None, False) or "properties" not in out:
            raise _Unsupported  # An open object (free-form keys) can't be expressed.
        out["additionalProperties"] = False
    return out


def claude_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """The schema in the subset Claude's structured outputs accept, or None when it can't be
    expressed (open objects, recursive references); the request then relies on the schema in the
    prompt, as it does for every other model."""
    if not isinstance(schema, dict):
        return None
    definitions = {**(schema.get("$defs") or {}), **(schema.get("definitions") or {})}
    if _recursive(definitions):
        return None
    try:
        return _clean(schema)
    except (_Unsupported, TypeError, ValueError):
        return None


def _usage(raw: dict[str, Any]) -> dict[str, int]:
    def number(key: str) -> int:
        value = raw.get(key)
        return value if isinstance(value, int) else 0
    prompt = number("input_tokens") + number("cache_creation_input_tokens") + number("cache_read_input_tokens")
    completion = number("output_tokens")
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
            "cache_read_tokens": number("cache_read_input_tokens"),
            "cache_write_tokens": number("cache_creation_input_tokens")}


def _same_model(returned: Any, requested: str) -> bool:
    return isinstance(returned, str) and (returned == requested or returned.startswith(requested + "-"))


class AnthropicMessagesProvider(ConfiguredChatProvider):
    """Claude Messages API. Admission, credentials, profiles and fallback are shared with every
    other configured text connection."""

    def body(self, model: str, request: Any, *, structured: bool = True) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": wire_model(self.connection, model),
            "max_tokens": generation_allowance(self.planner_profile, request.max_output_tokens),
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": bool(request.stream),
        }
        if request.system:
            body["system"] = [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}]
        output_config: dict[str, Any] = {}
        effort = self.planner_profile.request_parameters.get("reasoning_effort")
        if effort in EFFORTS:
            output_config["effort"] = effort
        if structured and request.response_format == "json_object":
            schema = claude_schema(request.json_schema)
            if schema is not None:
                output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            body["output_config"] = output_config
        return body

    async def generate_text(self, *, model, request):
        self.validate_request(model=model, request=request)
        body = self.body(model, request)
        try:
            return await self._send(model, body)
        except _SchemaRejected:
            # Claude refused the expressed schema; the schema in the prompt still applies.
            logger.warning("Claude rejected the structured output schema; retrying without it model=%s", model)
            return await self._send(model, self.body(model, request, structured=False))

    async def _send(self, model: str, body: dict[str, Any]) -> AIResponse:
        requested = body["model"]
        endpoint = self.connection.base_url + "/messages"
        stream = bool(body["stream"])
        logger.info("AI transport started connection=%s endpoint=%s hosting_provider=%s requested_model=%s",
                    self.name, endpoint, self.connection.hosting_provider, requested)
        headers = {"x-api-key": self.credential or "", "anthropic-version": ANTHROPIC_VERSION,
                   "content-type": "application/json"}
        try:
            async with (
                httpx.AsyncClient(timeout=transport_timeout(self.timeout_seconds, stream=stream),
                                  transport=self.transport, follow_redirects=False) as client,
                client.stream("POST", endpoint, headers=headers, json=body) as response,
            ):
                if response.status_code >= 300:
                    raw = (await response.aread())[:4096]
                    raise self._http_error(response, raw, structured="format" in body.get("output_config", {}))
                if stream:
                    payload = await self._collect(response, requested)
                else:
                    await response.aread()
                    payload = response.json()
        except (AIProviderError, _SchemaRejected):
            raise
        except httpx.TimeoutException as exc:
            raise timeout_error(exc, total_seconds=self.timeout_seconds, stream=stream,
                                message="Text transport timed out.") from exc
        except httpx.HTTPError as exc:
            raise AIProviderError("provider_unavailable", "Text endpoint could not be reached.", retryable=True) from exc
        except (ValueError, TypeError) as exc:
            raise AIProviderError("invalid_provider_response", "Unreadable text response.", retryable=False,
                                  rejection_reason="invalid_envelope") from exc
        return self._response(payload, model, requested, response.headers.get("request-id"))

    def _http_error(self, response: httpx.Response, raw: bytes, *, structured: bool) -> Exception:
        status = response.status_code
        error: dict[str, Any] = {}
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            error = (parsed.get("error") or {}) if isinstance(parsed, dict) else {}
        except (UnicodeDecodeError, ValueError):
            pass
        kind = str(error.get("type") or "")
        # The message is used only to classify; it is never logged or shown.
        message = str(error.get("message") or "").lower()
        details: dict[str, Any] = error["details"] if isinstance(error.get("details"), dict) else {}
        if ("credit balance" in message or "usage limits" in message
                or details.get("error_code") == "enforced_spend_limit_reached"):
            return AIProviderError("quota_exhausted", "The Claude account has no credit or reached its spend limit.",
                                   retryable=False, status_code=status, account_scoped=True, retry_after_seconds=3600)
        if status == 400 and structured and any(word in message for word in ("schema", "output_config", "format")):
            return _SchemaRejected()
        if status == 529 or kind == "overloaded_error":
            return AIProviderError("provider_unavailable", "Claude is overloaded right now.", retryable=True,
                                   status_code=status, retry_after_seconds=_retry_after(response.headers.get("retry-after")))
        category, retryable = _error_category(status, "")
        return AIProviderError(category, f"Text endpoint returned HTTP {status}.", retryable=retryable,
                               status_code=status, account_scoped=status in {401, 403, 429},
                               retry_after_seconds=_retry_after(response.headers.get("retry-after")))

    async def _collect(self, response: httpx.Response, requested: str) -> dict[str, Any]:
        """A streamed message, assembled privately. Thinking is never kept."""
        message: dict[str, Any] = {}
        parts: list[str] = []
        usage: dict[str, Any] = {}
        stop = None
        received = 0
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            received += len(data.encode())
            if received > 2_000_000:
                raise AIProviderError("invalid_provider_response", "Planning stream exceeded its evidence limit.",
                                      retryable=False)
            progress = cast(Any, self.planner_progress)
            if progress:
                await progress(received)
            event = json.loads(data)
            if not isinstance(event, dict):
                raise TypeError("Invalid stream event")
            kind = event.get("type")
            if kind == "error":
                raise AIProviderError("provider_unavailable", "The planning response stream was interrupted.",
                                      retryable=True)
            if kind == "message_start" and isinstance(event.get("message"), dict):
                message = event["message"]
                usage.update(message.get("usage") or {})
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                    parts.append(delta["text"])
            elif kind == "message_delta":
                stop = (event.get("delta") or {}).get("stop_reason") or stop
                usage.update(event.get("usage") or {})
            elif kind == "message_stop":
                break
        return {"type": "message", "id": message.get("id"), "model": message.get("model", requested),
                "content": [{"type": "text", "text": "".join(parts)}], "stop_reason": stop, "usage": usage}

    def _response(self, payload: Any, model: str, requested: str, request_id: str | None) -> AIResponse:
        if not isinstance(payload, dict) or payload.get("type") != "message" or not _same_model(payload.get("model"), requested):
            raise AIProviderError("invalid_provider_response", "Unexpected response model identity.",
                                  retryable=False, rejection_reason="model_mismatch")
        stop = payload.get("stop_reason")
        if stop == "refusal":
            raise AIProviderError("content_rejected", "Model declined the request.", retryable=False)
        content = payload.get("content")
        text = "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
                       ) if isinstance(content, list) else ""
        text = text.strip()
        if stop not in {"end_turn", "stop_sequence"} or not text:
            raise AIProviderError(
                "context_too_large" if stop == "model_context_window_exceeded" else "invalid_provider_response",
                "Text response was empty or incomplete.", retryable=False,
                rejection_reason=("truncated_output" if stop == "max_tokens" else "empty_output" if not text
                                  else "incomplete_output"))
        logger.info("AI transport received connection=%s hosting_provider=%s returned_model=%s",
                    self.name, self.connection.hosting_provider, payload.get("model"))
        return AIResponse(text=text, provider=self.name, model=model,
                          usage=_usage(usage if isinstance(usage := payload.get("usage"), dict) else {}),
                          request_id=request_id or payload.get("id"))


class _SchemaRejected(Exception):
    pass
