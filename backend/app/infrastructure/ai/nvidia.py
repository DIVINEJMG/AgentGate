from __future__ import annotations

import json

import httpx

from app.domain.ai.providers import (
    AIErrorCategory,
    AIMediaInput,
    AIProviderError,
    AIResponse,
    AITextRequest,
    ModelProviderCapabilities,
)


def _error_category(status_code: int, detail: str) -> tuple[AIErrorCategory, bool]:
    lowered = detail.lower()
    if status_code in {401, 403}:
        return "authentication_failed", False
    if status_code == 404:
        return "model_not_found", False
    if status_code == 429:
        return "rate_limited", True
    if status_code in {408, 504}:
        return "timeout", True
    if status_code == 413 or "context" in lowered and "long" in lowered:
        return "context_too_large", False
    if status_code >= 500:
        return "provider_unavailable", True
    if "safety" in lowered or "content" in lowered and "reject" in lowered:
        return "content_rejected", False
    return "invalid_provider_response", False


def _detail_from_bytes(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")[:400]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])[:400]
        if isinstance(payload.get("detail"), str):
            return str(payload["detail"])[:400]
    return str(payload)[:400]


def _content_from_payload(payload: dict[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
        return "\n".join(parts).strip()
    return ""


def _usage_from_payload(payload: dict[str, object]) -> dict[str, int]:
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw.get(key)
        if isinstance(value, int):
            result[key] = value
    return result


class NvidiaNimProvider:
    name = "nvidia_nim"
    capabilities = ModelProviderCapabilities(
        text_input=True,
        image_input=True,
        structured_json=True,
        tool_calls=True,
        streaming=True,
        reasoning_output=False,
        context_window_tokens=None,
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        timeout_seconds: int = 60,
        extra_body: dict[str, object] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = max(10, timeout_seconds)
        self._extra_body = dict(extra_body or {})
        self._transport = transport

    def _headers(self, correlation_id: str | None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id[:128]
        return headers

    def _body(
        self,
        *,
        model: str,
        request: AITextRequest,
        messages: list[dict[str, object]],
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": max(16, request.max_output_tokens),
            "stream": request.stream,
        }
        if request.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        if request.stream:
            body["stream_options"] = {"include_usage": True}
        body.update(self._extra_body)
        return body

    async def _raise_for_error(self, response: httpx.Response) -> None:
        raw = await response.aread()
        detail = _detail_from_bytes(raw)
        category, retryable = _error_category(response.status_code, detail)
        message = f"NVIDIA NIM request failed with HTTP {response.status_code}."
        if detail:
            message += f" {detail}"
        raise AIProviderError(
            category,
            message,
            retryable=retryable,
            status_code=response.status_code,
        )

    async def _stream_text(
        self,
        response: httpx.Response,
    ) -> tuple[str, dict[str, int]]:
        parts: list[str] = []
        usage: dict[str, int] = {}
        async for line in response.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            usage.update(_usage_from_payload(payload))
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            first = choices[0]
            if not isinstance(first, dict):
                continue
            delta = first.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                parts.append(str(delta["content"]))
        return "".join(parts).strip(), usage

    async def _invoke(
        self,
        *,
        model: str,
        request: AITextRequest,
        messages: list[dict[str, object]],
    ) -> AIResponse:
        body = self._body(model=model, request=request, messages=messages)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                if request.stream:
                    async with client.stream(
                        "POST",
                        f"{self._base_url}/chat/completions",
                        headers=self._headers(request.correlation_id),
                        json=body,
                    ) as response:
                        if response.status_code >= 400:
                            await self._raise_for_error(response)
                        text, usage = await self._stream_text(response)
                        request_id = response.headers.get("x-request-id")
                else:
                    response = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers=self._headers(request.correlation_id),
                        json=body,
                    )
                    if response.status_code >= 400:
                        await self._raise_for_error(response)
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise AIProviderError(
                            "invalid_provider_response",
                            "NVIDIA NIM returned an invalid response envelope.",
                            retryable=False,
                        )
                    text = _content_from_payload(payload)
                    usage = _usage_from_payload(payload)
                    request_id = response.headers.get("x-request-id")
                    if not request_id and isinstance(payload.get("id"), str):
                        request_id = str(payload["id"])
        except AIProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise AIProviderError(
                "timeout",
                "NVIDIA NIM request timed out.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(
                "provider_unavailable",
                "NVIDIA NIM could not be reached.",
                retryable=True,
            ) from exc
        except (ValueError, TypeError) as exc:
            raise AIProviderError(
                "invalid_provider_response",
                "NVIDIA NIM returned an unreadable response.",
                retryable=False,
            ) from exc

        if not text:
            raise AIProviderError(
                "invalid_provider_response",
                "NVIDIA NIM returned no text output.",
                retryable=False,
            )
        return AIResponse(
            text=text,
            provider=self.name,
            model=model,
            request_id=request_id,
            usage=usage,
        )

    async def generate_text(self, *, model: str, request: AITextRequest) -> AIResponse:
        messages: list[dict[str, object]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        return await self._invoke(model=model, request=request, messages=messages)

    async def analyze_media(
        self,
        *,
        model: str,
        request: AITextRequest,
        media: AIMediaInput,
    ) -> AIResponse:
        if media.url:
            image_url = media.url
        elif media.data_base64:
            image_url = f"data:{media.media_type};base64,{media.data_base64}"
        else:
            raise AIProviderError(
                "invalid_provider_response",
                "Media input requires a URL or base64 payload.",
                retryable=False,
            )
        messages: list[dict[str, object]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": request.prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        )
        return await self._invoke(model=model, request=request, messages=messages)
