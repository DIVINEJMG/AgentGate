"""The Audoryn Gateway (docs/plans/architecture-scale-plan.md, section 5).

Phase 1a: every HTTP request, streamed reply and WebSocket reaches its service unchanged; the
gateway adds only a correlation id and one access log line per request.
Phase 1b: the route table picks the pool, and the pool picks a healthy copy that isn't draining.
A request that could not reach a copy is tried once on another; see `_may_retry`.
Phase 1c: the route group's traffic limits run before a copy is chosen (limits.py).

`/health/gateway` is the gateway's own check and `/gateway/admin/...` its controls. Every other
path is a service's, `/health/live` and `/health/ready` included.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from redis.asyncio import Redis
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from audoryn_gateway.edge import at_the_edge
from audoryn_gateway.headers import correlation_id, downstream_response, upstream_request
from audoryn_gateway.limits import Limiter, policy_for
from audoryn_gateway.pools import Copy, Pools
from audoryn_gateway.routes import pool_for
from audoryn_gateway.settings import GatewaySettings

logger = logging.getLogger("audoryn.gateway")
Scope = dict[str, Any]
_FAILURES = {413: "The request is too large.", 429: "Too many requests. Try again shortly.",
             502: "The service is unavailable.", 503: "Try again shortly.", 504: "The service did not answer in time."}
_WEBSOCKET_HEADERS = frozenset({"authorization", "cookie", "x-correlation-id", "x-forwarded-for", "x-forwarded-proto"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_LOOPBACK = frozenset({"127.0.0.1", "::1"})


class Gateway:
    def __init__(self, settings: GatewaySettings | None = None) -> None:
        self.settings = settings = settings or GatewaySettings()
        self.redis = Redis.from_url(settings.redis_url.get_secret_value(), socket_timeout=settings.redis_timeout_seconds,
                                    socket_connect_timeout=settings.redis_timeout_seconds) if settings.redis_url else None
        self.pools = Pools(settings, self.redis, settings.redis_prefix)
        if settings.limits_mode != "off" and settings.limit_secret is None:
            logger.warning("GATEWAY_LIMIT_SECRET is unset: session limits are kept per gateway process")
        secret = settings.limit_secret.get_secret_value() if settings.limit_secret else secrets.token_hex(32)
        self.limiter = Limiter(self.redis, secret.encode(), settings.policies, settings.limits_mode,
                               settings.redis_prefix)
        self._client: httpx.AsyncClient | None = None
        self._counting: set[asyncio.Task[object]] = set()

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
        elif scope["type"] == "lifespan":
            await self._lifespan(receive, send)

    async def _lifespan(self, receive: Any, send: Any) -> None:
        loops: list[asyncio.Task[None]] = []
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                loops = [asyncio.create_task(self.pools.watch(self.client)),
                         asyncio.create_task(self.limiter.follow(self.settings.policy_refresh_seconds))]
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                for loop in loops:
                    loop.cancel()
                await asyncio.gather(*loops, return_exceptions=True)
                await self.client.aclose()
                if self.redis is not None:
                    await self.redis.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            settings = self.settings
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=settings.connect_timeout_seconds, read=None,
                                      write=settings.write_timeout_seconds, pool=settings.connect_timeout_seconds),
                limits=httpx.Limits(max_connections=settings.max_connections,
                                    max_keepalive_connections=settings.max_connections,
                                    keepalive_expiry=settings.upstream_keepalive_seconds),
                follow_redirects=False, trust_env=False)
        return self._client

    async def _http(self, scope: Scope, receive: Any, send: Any) -> None:
        started = time.perf_counter()
        correlation = correlation_id(scope["headers"])
        path = scope["path"]
        if path == "/health/gateway":
            await _reply(send, 200, self._health(), correlation)
            return
        if path.startswith("/gateway/admin/"):
            await self._admin(scope, send, correlation)
            return
        pool = pool_for(path)
        if pool is None:  # the same answer the core gives a path it doesn't serve
            await _reply(send, 404, {"detail": "Not Found"}, correlation)
            _log(scope, 404, 0, started, correlation, None)
            return
        refused, retry_after, cap = await self._admit(scope)
        if refused:
            await _reply(send, refused, {"detail": _FAILURES[refused]}, correlation, retry_after)
            _log(scope, refused, 0, started, correlation, None)
            return
        headers = upstream_request(scope["headers"], client=_ip(scope), scheme=scope["scheme"], correlation=correlation)
        has_body = any(name in (b"content-length", b"transfer-encoding") for name, _ in scope["headers"])
        body = _body(receive, cap) if has_body else None
        copy, status, sent, failure = self.pools.choose(pool), 502, 0, None
        try:
            for attempt in (1, 2):
                if copy is None:
                    break
                with self.pools.using(copy):
                    try:
                        # Built directly, not with client.build_request: that would add httpx's default
                        # headers (Accept-Encoding among them), and the copy could reply in an encoding
                        # the caller never asked for.
                        response = await self.client.send(httpx.Request(
                            scope["method"], _target(scope, copy.url), headers=headers, content=body), stream=True)
                    except _TooLarge:
                        status = 413
                        await _reply(send, status, {"detail": _FAILURES[status]}, correlation)
                        return
                    except httpx.HTTPError as error:
                        failure = error
                        if isinstance(error, _NOT_SENT):
                            self.pools.unreachable(copy)
                        if attempt == 2 or not _may_retry(error, scope["method"], has_body):
                            break
                        copy = self.pools.retry_on(pool, copy)
                        continue
                    status, failure = response.status_code, None
                    sent = await _relay(response, receive, send, correlation)
                    break
            if failure is not None or copy is None:
                # A copy that was reached but stopped answering is a timeout; anything else is unavailable.
                reached = isinstance(failure, httpx.TimeoutException) and not isinstance(failure, _NOT_SENT)
                status = 504 if reached else 502
                await _reply(send, status, {"detail": _FAILURES[status]}, correlation)
        finally:
            _log(scope, status, sent, started, correlation, copy)

    async def _admit(self, scope: Scope, websocket: bool = False) -> tuple[int | None, int | None, int | None]:
        """The route group's limits: the status to refuse with (or None), its Retry-After, and the
        body cap to hold a body without Content-Length to (or None)."""
        policy = policy_for(scope.get("method", "GET"), scope["path"], websocket)
        mode = self.limiter.mode_of(policy)
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and self.limiter.body_too_large(policy, int(length)):
            logger.warning(json.dumps({"event": "gateway.limit", "mode": mode, "policy": policy,
                                       "path": scope["path"], "rule": f"body {int(length)} bytes"}, separators=(",", ":")))
            if mode == "enforce":
                return 413, None, None
        check = self.limiter.check(policy, ip=_ip(scope), token=_token(scope), path=scope["path"])
        if mode != "enforce":
            # Report mode refuses nothing, so the request doesn't wait for Redis: it is counted alongside.
            task = asyncio.create_task(check)
            self._counting.add(task)
            task.add_done_callback(self._counting.discard)
            return None, None, None
        decision = await check
        refused = decision.refuse
        cap = self.limiter.policies[policy].max_body_bytes if policy and mode == "enforce" else None
        return refused, (decision.retry_after() if refused else None), cap

    def _health(self) -> dict[str, object]:
        pools = {name: {"inRotation": sum(copy.healthy and not copy.draining for copy in copies),
                        "copies": len(copies)} for name, copies in self.pools.copies.items()}
        return {"status": "ok", "service": "audoryn-gateway", "pools": pools,
                "limits": {"mode": self.limiter.mode, "consoleVersion": self.limiter.console_version}}

    async def _admin(self, scope: Scope, send: Any, correlation: str) -> None:
        """Lists, drains and restores copies; from this machine, with the admin token. Anything
        else gets the 404 an unknown path gets, so the controls aren't visible from outside."""
        token = self.settings.admin_token
        given = dict(scope["headers"]).get(b"x-gateway-admin-token", b"").decode("latin-1")
        client = scope.get("client")
        parts = scope["path"].removeprefix("/gateway/admin/").strip("/").split("/")
        allowed = (token is not None and client is not None and client[0] in _LOOPBACK
                   and hmac.compare_digest(given, token.get_secret_value()))
        if allowed and scope["method"] == "GET" and parts == ["copies"]:
            copies = [{**copy.as_dict(), "pools": self.pools.pools_of(copy)} for copy in self.pools.all()]
            await _reply(send, 200, {"copies": copies}, correlation)
            return
        copy = self.pools.find(parts[1]) if len(parts) == 3 and parts[0] == "copies" else None
        if allowed and scope["method"] == "POST" and copy and parts[2] in ("drain", "restore"):
            # `?last=true` drains it anyway: its pool answers 502 until it is restored. Used to
            # restart the only execution copy (QStash retries its deliveries meanwhile).
            if (parts[2] == "drain" and not copy.draining and self.pools.last_in_rotation(copy)
                    and scope["query_string"] != b"last=true"):
                await _reply(send, 409, {"detail": f"{copy.name} is the last copy in rotation."}, correlation)
                return
            await self.pools.set_draining(copy, parts[2] == "drain")
            logger.warning("Upstream %s %s by the admin", copy.name, "draining" if copy.draining else "restored")
            await _reply(send, 200, copy.as_dict(), correlation)
            return
        await _reply(send, 404, {"detail": "Not Found"}, correlation)

    async def _websocket(self, scope: Scope, receive: Any, send: Any) -> None:
        started = time.perf_counter()
        correlation = correlation_id(scope["headers"])
        await receive()  # websocket.connect
        pool = pool_for(scope["path"])
        refused, _, _ = await self._admit(scope, websocket=True) if pool else (None, None, None)
        if refused:
            await send({"type": "websocket.close", "code": 1008})  # a refused handshake (403)
            _log(scope, refused, 0, started, correlation, None)
            return
        names = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in upstream_request(
            scope["headers"], client=_ip(scope), scheme="https" if scope["scheme"] == "wss" else "http",
            correlation=correlation)}
        copy, status, upstream = (self.pools.choose(pool) if pool else None), 502, None
        for attempt in (1, 2):
            if copy is None:
                break
            try:
                upstream = await connect(
                    _target(scope, "ws" + copy.url.removeprefix("http")),
                    additional_headers={name: value for name, value in names.items() if name in _WEBSOCKET_HEADERS},
                    origin=names.get("origin"), subprotocols=scope.get("subprotocols") or None,  # type: ignore[arg-type]
                    open_timeout=self.settings.connect_timeout_seconds, ping_interval=None, max_size=None)
                break
            except InvalidStatus as refused:  # the copy refused it (its auth check closes before accepting)
                status = refused.response.status_code
                break
            except (InvalidHandshake, OSError, TimeoutError):
                self.pools.unreachable(copy)
                copy = self.pools.choose(pool, avoid=copy) if attempt == 1 and pool else None
        if upstream is None:
            # Refused or unreachable: the caller is refused the same way, a 403 handshake.
            await send({"type": "websocket.close", "code": 1008})
            _log(scope, status if pool else 404, 0, started, correlation, copy)
            return
        assert copy is not None
        await send({"type": "websocket.accept", "subprotocol": upstream.subprotocol,
                    "headers": [(b"x-correlation-id", correlation.encode())]})
        _log(scope, 101, 0, started, correlation, copy)
        with self.pools.using(copy, socket=True):
            await _pipe(upstream, receive, send)


def _may_retry(error: httpx.HTTPError, method: str, has_body: bool) -> bool:
    """Another copy may take the request when this one never received it (no connection), or when
    the request has no side effects or body (GET, HEAD, OPTIONS) and the connection dropped before
    any reply. A POST that may have arrived is never sent twice; QStash retries its own deliveries."""
    return isinstance(error, _NOT_SENT) or (
        method in _SAFE_METHODS and not has_body and isinstance(error, httpx.TransportError))


class _TooLarge(Exception):
    """A body without Content-Length passed its route group's cap."""


def _ip(scope: Scope) -> str | None:
    client = scope.get("client")
    return client[0] if client else None


def _token(scope: Scope) -> str | None:
    """The caller's session token: the bearer token, or the WebSocket's `access_token`."""
    authorization = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
    if authorization.lower().startswith("bearer ") and authorization[7:].strip():
        return authorization[7:].strip()
    for pair in scope.get("query_string", b"").decode("latin-1").split("&"):
        name, _, value = pair.partition("=")
        if name == "access_token" and value:
            return value
    return None


def _target(scope: Scope, base: str) -> str:
    path = (scope.get("raw_path") or scope["path"].encode()).decode("latin-1")
    query = scope.get("query_string", b"").decode("latin-1")
    return f"{base}{path}" + (f"?{query}" if query else "")


async def _body(receive: Any, cap: int | None = None) -> AsyncIterator[bytes]:
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        size += len(message.get("body", b""))
        if cap is not None and size > cap:
            raise _TooLarge
        yield message.get("body", b"")
        if not message.get("more_body"):
            return


async def _relay(response: httpx.Response, receive: Any, send: Any, correlation: str) -> int:
    """Streams the reply to the caller and stops as soon as the caller goes away, so an open stream
    (server-sent events) never outlives its reader."""
    sent = 0

    async def stream() -> None:
        nonlocal sent
        await send({"type": "http.response.start", "status": response.status_code,
                    "headers": downstream_response(response.headers.raw, correlation=correlation)})
        async for chunk in response.aiter_raw():
            sent += len(chunk)
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def caller_gone() -> None:
        while (await receive())["type"] != "http.disconnect":
            pass

    tasks = (asyncio.create_task(stream()), asyncio.create_task(caller_gone()))
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await response.aclose()
    return sent


async def _pipe(upstream: ClientConnection, receive: Any, send: Any) -> None:
    """Relays messages both ways until either side closes, passing the close code on."""

    async def from_caller() -> None:
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                await upstream.close(message.get("code", 1000))
                return
            await upstream.send(message["text"] if message.get("text") is not None else message.get("bytes") or b"")

    async def from_service() -> None:
        try:
            async for message in upstream:
                await send({"type": "websocket.send", **({"text": message} if isinstance(message, str) else {"bytes": message})})
        except ConnectionClosed:
            pass
        await send({"type": "websocket.close", "code": upstream.close_code or 1000, "reason": upstream.close_reason or ""})

    tasks = (asyncio.create_task(from_caller()), asyncio.create_task(from_service()))
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await upstream.close()


async def _reply(send: Any, status: int, body: dict[str, object], correlation: str,
                 retry_after: int | None = None) -> None:
    payload = json.dumps(body, separators=(",", ":")).encode()  # compact, like the core's replies
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()),
        (b"x-correlation-id", correlation.encode()),
        *([(b"retry-after", str(retry_after).encode())] if retry_after else [])]})
    await send({"type": "http.response.body", "body": payload})


def _log(scope: Scope, status: int, sent: int, started: float, correlation: str, copy: Copy | None) -> None:
    """One line per request. The path only: query strings can carry tokens (the WebSocket's
    `access_token`), so they are never logged, and neither are headers."""
    logger.info(json.dumps({
        "event": "gateway.request", "method": scope.get("method", "WEBSOCKET"), "path": scope["path"],
        "status": status, "latencyMs": round((time.perf_counter() - started) * 1000, 1), "bytes": sent,
        "upstream": copy.name if copy else None, "correlationId": correlation}, separators=(",", ":")))


gateway = Gateway()
app = at_the_edge(gateway, gateway.settings)
