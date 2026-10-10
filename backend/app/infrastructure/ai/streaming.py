"""Accumulate streamed proposals privately; publish only transport metadata."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx

from app.domain.ai.providers import AIProviderError


async def collect_response(
    response: httpx.Response,
    *,
    model: str,
    accepted_models: set[str] | None = None,
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> dict:
    parts: list[str] = []
    usage, identity, finish, refusal = {}, None, None, None
    received = 0
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError as exc:
            raise AIProviderError(
                "invalid_provider_response", "Unreadable streamed response.", retryable=False
            ) from exc
        if not isinstance(chunk, dict):
            raise AIProviderError(
                "invalid_provider_response", "Invalid streamed response envelope.", retryable=False
            )
        received += len(data.encode())
        if received > 2_000_000:
            raise AIProviderError(
                "invalid_provider_response",
                "Planning stream exceeded its evidence limit.",
                retryable=False,
            )
        if progress:
            await progress(received)
        if chunk.get("error"):
            raise AIProviderError(
                "provider_unavailable",
                "The planning response stream was interrupted.",
                retryable=True,
            )
        if chunk.get("model", model) not in (accepted_models or {model}):
            raise AIProviderError(
                "invalid_provider_response", "Unexpected streamed model identity.", retryable=False
            )
        identity = chunk.get("id", identity)
        if chunk.get("usage") and not isinstance(chunk["usage"], dict):
            raise AIProviderError(
                "invalid_provider_response", "Invalid stream usage metadata.", retryable=False
            )
        usage.update(chunk.get("usage") or {})
        choices = chunk.get("choices") or []
        if not choices:
            continue
        if not isinstance(choices, list) or not isinstance(choices[0], dict):
            raise AIProviderError(
                "invalid_provider_response", "Invalid stream choices.", retryable=False
            )
        delta = choices[0].get("delta") or {}
        if not isinstance(delta, dict):
            raise AIProviderError(
                "invalid_provider_response", "Invalid stream delta.", retryable=False
            )
        if delta.get("refusal"):
            refusal = True
        if isinstance(delta.get("content"), str):
            parts.append(delta["content"])
        # Reasoning fields are deliberately ignored and never published.
        finish = choices[0].get("finish_reason") or finish
    if refusal or finish == "content_filter":
        raise AIProviderError("content_rejected", "Planner declined this request.", retryable=False)
    if finish != "stop":
        raise AIProviderError(
            "invalid_provider_response",
            "Planning stream ended without a complete response.",
            retryable=False,
            rejection_reason="truncated_output" if finish == "length" else "incomplete_output",
        )
    return {
        "id": identity,
        "model": model,
        "usage": usage,
        "choices": [{"finish_reason": finish, "message": {"content": "".join(parts)}}],
    }
