"""Accumulate streamed proposals privately; publish only transport metadata."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx

from audoryn_shared.domain.ai.providers import AIProviderError


async def collect_response(
    response: httpx.Response,
    *,
    model: str,
    accepted_models: set[str] | None = None,
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> dict:
    parts: list[str] = []
    usage, identity, finish, refusal = {}, None, None, False
    received = 0
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = _chunk(data)
        received += len(data.encode())
        if received > 2_000_000:
            raise AIProviderError(
                "invalid_provider_response",
                "Planning stream exceeded its evidence limit.",
                retryable=False,
            )
        if progress:
            await progress(received)
        _check_chunk(chunk, model, accepted_models)
        identity = chunk.get("id", identity)
        usage.update(chunk.get("usage") or {})
        choice = _choice(chunk)
        if choice is None:
            continue
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            raise AIProviderError(
                "invalid_provider_response", "Invalid stream delta.", retryable=False
            )
        refusal = refusal or bool(delta.get("refusal"))
        if isinstance(delta.get("content"), str):
            parts.append(delta["content"])
        # Reasoning fields are deliberately ignored and never published.
        finish = choice.get("finish_reason") or finish
    _raise_unless_finished(finish, refusal)
    return {
        "id": identity,
        "model": model,
        "usage": usage,
        "choices": [{"finish_reason": finish, "message": {"content": "".join(parts)}}],
    }


def _chunk(data: str) -> dict:
    """One streamed event, which must be a JSON object."""
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
    return chunk


def _check_chunk(chunk: dict, model: str, accepted_models: set[str] | None) -> None:
    """A streamed event reports no error, comes from the requested model and carries valid usage."""
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
    if chunk.get("usage") and not isinstance(chunk["usage"], dict):
        raise AIProviderError(
            "invalid_provider_response", "Invalid stream usage metadata.", retryable=False
        )


def _choice(chunk: dict) -> dict | None:
    """The event's first choice, or None for an event without one (usage only)."""
    choices = chunk.get("choices") or []
    if not choices:
        return None
    if not isinstance(choices, list) or not isinstance(choices[0], dict):
        raise AIProviderError(
            "invalid_provider_response", "Invalid stream choices.", retryable=False
        )
    return choices[0]


def _raise_unless_finished(finish: object, refusal: bool) -> None:
    """A stream must end with `stop`; a refusal declines, anything else is incomplete (and says how
    it ended, F33)."""
    if refusal or finish == "content_filter":
        raise AIProviderError("content_rejected", "Planner declined this request.", retryable=False)
    if finish != "stop":
        raise AIProviderError(
            "invalid_provider_response",
            "Planning stream ended without a complete response.",
            retryable=False,
            rejection_reason="truncated_output" if finish == "length" else "incomplete_output",
            finish_reason=finish,
        )


def transport_timeout(total_seconds: float, *, stream: bool) -> httpx.Timeout:
    """A streamed response may take the whole budget while bytes keep arriving; only a silence
    longer than `planner_stream_silence_seconds` ends it. Other requests keep one flat limit."""
    from audoryn_shared.bootstrap.settings import settings

    silence = settings.planner_stream_silence_seconds
    if stream and silence < total_seconds:
        return httpx.Timeout(total_seconds, read=silence)
    return httpx.Timeout(total_seconds)


def timeout_error(error: httpx.TimeoutException, *, total_seconds: float, stream: bool, message: str) -> AIProviderError:
    """A silent stream is a failed connection (handled like an unreachable endpoint), not a slow
    model; any other timeout keeps its meaning."""
    from audoryn_shared.bootstrap.settings import settings

    silence = settings.planner_stream_silence_seconds
    if stream and silence < total_seconds and isinstance(error, httpx.ReadTimeout):
        return AIProviderError("provider_unavailable", f"The response stream sent nothing for {silence} seconds.",
                               retryable=True, rejection_reason="stream_silent")
    return AIProviderError("timeout", message, retryable=True)
