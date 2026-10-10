"""Request handling shared by every Console read route.

Order: feature switch -> signature (scope from the matched route) -> replay claim ->
rate limit -> parameter check -> producer -> envelope. Every failure becomes the
contract's error body; reasons and exception types go to the log only.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Protocol
from urllib.parse import parse_qsl
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.application.console_reads.config import ConsoleReadsConfig, load_config
from app.application.console_reads.signing import (
    ReplayProtection,
    ReplayStoreUnavailable,
    ServiceRequestRejected,
    verify_read_request,
)

logger = logging.getLogger("audoryn.console_reads")

MAX_RESPONSE_BYTES = 1024 * 1024
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_MESSAGES = {
    "not_found": "Not found.",
    "unauthorized": "Service request was not authorized.",
    "invalid_parameters": "Request parameters are invalid.",
    "rate_limited": "Too many requests.",
    "unavailable": "Service temporarily unavailable.",
    "timeout": "The read took too long.",
}
_STATUS = {
    "not_found": 404,
    "unauthorized": 401,
    "invalid_parameters": 400,
    "rate_limited": 429,
    "unavailable": 503,
    "timeout": 504,
}


class ConsoleReadError(Exception):
    def __init__(self, code: str, *, reason: str | None = None, retry_after: int | None = None) -> None:
        self.code = code
        self.reason = reason or code
        self.retry_after = retry_after
        super().__init__(code)


class InvalidParameters(ValueError):
    """Raised by producers for a parameter outside its bounds."""


class RateLimiter(Protocol):
    async def hit(self, *, key_id: str, limit: int, now: int) -> int | None:
        """Count one request; return seconds to wait when over the limit, else None.

        Raise ReplayStoreUnavailable when the store cannot answer.
        """
        ...


class RedisReplayProtection:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def claim(self, *, key_id: str, nonce: str, ttl_seconds: int) -> bool:
        try:
            return bool(
                await self._client.set(f"console-reads:replay:{key_id}:{nonce}", "1", nx=True, ex=ttl_seconds)
            )
        except Exception as exc:  # any Redis failure fails closed
            raise ReplayStoreUnavailable(type(exc).__name__) from exc


class RedisRateLimiter:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def hit(self, *, key_id: str, limit: int, now: int) -> int | None:
        window = now // 60
        key = f"console-reads:rate:{key_id}:{window}"
        try:
            count = int(await self._client.incr(key))
            if count == 1:
                await self._client.expire(key, 120)
        except Exception as exc:
            raise ReplayStoreUnavailable(type(exc).__name__) from exc
        return None if count <= limit else max(1, (window + 1) * 60 - now)


QueryRunner = Callable[..., Awaitable[Any]]


@dataclass
class ConsoleReadsRuntime:
    config: ConsoleReadsConfig
    replay: ReplayProtection | None = None
    rate_limiter: RateLimiter | None = None
    clock: Callable[[], float] = time.time
    # Runs a sync query function read-only: ``await runner(query, timeout_ms=...)``.
    # None means the production runner (``database.run_read_only``); tests supply their own.
    query_runner: QueryRunner | None = None


@lru_cache
def _runtime_from_settings() -> ConsoleReadsRuntime:
    from app.bootstrap.settings import settings

    config = load_config(settings)
    if not config.enabled:
        return ConsoleReadsRuntime(config=config)
    from redis.asyncio import Redis

    client = Redis.from_url(settings.redis_dsn, decode_responses=True)
    return ConsoleReadsRuntime(config=config, replay=RedisReplayProtection(client), rate_limiter=RedisRateLimiter(client))


def console_reads_runtime() -> ConsoleReadsRuntime:
    """FastAPI dependency; tests override it through ``app.dependency_overrides``."""
    return _runtime_from_settings()


@dataclass(frozen=True)
class ReadContext:
    operation: str
    target: str
    request_id: str
    key_id: str
    parameters: Mapping[str, str]
    config: ConsoleReadsConfig


Producer = Callable[[ReadContext], Awaitable[object]]


def _target(request: Request) -> str:
    raw_path = request.scope.get("raw_path") or request.scope["path"].encode()
    query = request.scope.get("query_string", b"")
    try:
        path = raw_path.decode("ascii")
        return path + ("?" + query.decode("ascii") if query else "")
    except UnicodeDecodeError as exc:
        raise ConsoleReadError("unauthorized", reason="invalid_target") from exc


def _parameters(target: str, allowed: frozenset[str]) -> dict[str, str]:
    query = target.partition("?")[2]
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=bool(query), errors="strict")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConsoleReadError("invalid_parameters", reason="unparseable_query") from exc
    values: dict[str, str] = {}
    for name, value in pairs:
        if name not in allowed or name in values:
            raise ConsoleReadError("invalid_parameters", reason="unexpected_or_repeated_parameter")
        values[name] = value
    return values


def _error_response(error: ConsoleReadError, request_id: str) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
    if error.retry_after is not None:
        headers["Retry-After"] = str(error.retry_after)
    return JSONResponse(
        {"version": 1, "error": {"code": error.code, "message": _MESSAGES[error.code]}},
        status_code=_STATUS[error.code],
        headers=headers,
    )


async def serve(
    request: Request,
    runtime: ConsoleReadsRuntime,
    *,
    operation: str,
    scope: str,
    producer: Producer,
    parameters: frozenset[str] = frozenset(),
) -> Response:
    started = time.perf_counter()
    supplied = request.headers.get("x-request-id", "")
    request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid4().hex
    key_id = "-"
    try:
        config = runtime.config
        if not config.enabled or runtime.replay is None or runtime.rate_limiter is None:
            raise ConsoleReadError("not_found", reason="disabled")
        target = _target(request)
        now = int(runtime.clock())
        try:
            key = await verify_read_request(
                keys=config.keys,
                audience=config.audience,
                required_scope=scope,
                method=request.method,
                target=target,
                body=await request.body(),
                headers=request.headers.items(),
                replay=runtime.replay,
                now=now,
                max_clock_skew_seconds=config.clock_skew_seconds,
            )
        except ServiceRequestRejected as exc:
            raise ConsoleReadError("unauthorized", reason=exc.reason) from exc
        key_id = key.key_id
        wait = await runtime.rate_limiter.hit(key_id=key_id, limit=config.rate_per_minute, now=now)
        if wait is not None:
            raise ConsoleReadError("rate_limited", retry_after=wait)
        context = ReadContext(
            operation=operation,
            target=target,
            request_id=request_id,
            key_id=key_id,
            parameters=_parameters(target, parameters),
            config=config,
        )
        try:
            data = await producer(context)
        except InvalidParameters as exc:
            raise ConsoleReadError("invalid_parameters", reason="parameter_bounds") from exc
        body = json.dumps(
            {
                "version": 1,
                "operation": operation,
                "requestTarget": target,
                "requestId": request_id,
                "observedAt": datetime.now(UTC).isoformat(),
                "data": data,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if len(body) > MAX_RESPONSE_BYTES:
            raise ConsoleReadError("unavailable", reason="response_too_large")
        response: Response = Response(
            body,
            media_type="application/json",
            headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
        )
        outcome, reason = "ok", None
    except ConsoleReadError as exc:
        response = _error_response(exc, request_id)
        outcome, reason = exc.code, exc.reason
    except ReplayStoreUnavailable as exc:
        response = _error_response(ConsoleReadError("unavailable"), request_id)
        outcome, reason = "unavailable", f"redis:{exc}"
    except TimeoutError:
        response = _error_response(ConsoleReadError("timeout"), request_id)
        outcome, reason = "timeout", "timeout"
    except Exception as exc:  # noqa: BLE001 - never leak internals to the caller; log only the type
        response = _error_response(ConsoleReadError("unavailable"), request_id)
        outcome, reason = "unavailable", type(exc).__name__
    # Operation, key, outcome and timing only: never query values, headers or bodies.
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    # Refusals and failures log at WARNING so they are visible without logging configuration.
    # Fields are in the message too, because the default formatter drops ``extra``.
    logger.log(
        logging.INFO if outcome == "ok" else logging.WARNING,
        "console_reads.request operation=%s key=%s outcome=%s reason=%s status=%s duration_ms=%s request_id=%s",
        operation,
        key_id,
        outcome,
        reason,
        response.status_code,
        duration_ms,
        request_id,
        extra={
            "operation": operation,
            "key_id": key_id,
            "outcome": outcome,
            "reason": reason,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "request_id": request_id,
        },
    )
    return response
