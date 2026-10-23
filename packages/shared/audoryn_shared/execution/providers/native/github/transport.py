"""GitHub-only HTTP policy. Other integrations retain their own semantics."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from redis.exceptions import RedisError

from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.execution.contracts import ExecutionErrorCode
from audoryn_shared.execution.providers.native.http import (
    ProviderTransportError,
    retry_after_seconds,
)
from audoryn_shared.infrastructure.redis.coordination import RedisCoordinator

account_context: ContextVar[str] = ContextVar("github_account", default="")
TRUSTED_DOWNLOAD_HOSTS = {"api.github.com", "codeload.github.com", "uploads.github.com"}
# One request at a time per installation; a request finding it busy waits this long for its turn
# (a GitHub call normally takes well under a second) before the step's own retry takes over.
TURN_WAIT_SECONDS = 15.0


async def _pace(coordinator, key: str, method: str) -> None:
    """Respect the installation's cool-down, and space its writes at least a second apart."""
    cooldown = float(await coordinator.cache_get(key + ":cooldown") or "0")
    if cooldown > time.time():
        raise ProviderTransportError(
            code="rate_limited",
            retryable=True,
            retry_after_seconds=cooldown - time.time(),
            safe_message="This GitHub installation is cooling down. Other integrations remain available.",
        )
    if method != "GET":
        last = float(await coordinator.cache_get(key + ":last-write") or "0")
        await asyncio.sleep(max(0.0, 1.0 - (time.time() - last)))
        await coordinator.cache_set(key + ":last-write", str(time.time()), ttl_seconds=120)


def _redirect_target(response, binary: bool) -> tuple[str, str | None]:
    """A redirect's target and host, refused unless it is an approved GitHub host (or, for a binary
    download, a signed artifact URL, which never receives the installation token)."""
    target = response.headers.get("location", "")
    parts = urlsplit(target)
    allowed = parts.hostname in TRUSTED_DOWNLOAD_HOSTS or (
        binary and parts.hostname and parts.hostname.endswith(".blob.core.windows.net")
    )
    if not allowed or parts.scheme != "https" or parts.username:
        raise ProviderTransportError(
            code="policy_blocked",
            retryable=False,
            safe_message="GitHub redirect is outside trusted download destinations.",
        )
    return target, parts.hostname


async def _read_body(response, max_bytes: int) -> bytearray:
    """The response body, refused once it exceeds the action's evidence budget."""
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > max_bytes:
            raise ProviderTransportError(
                code="validation_error",
                retryable=False,
                safe_message="GitHub response exceeds this action's evidence size budget.",
            )
    return raw


async def _wait_for_turn(coordinator, key: str):
    """The installation's request lock, waited for up to TURN_WAIT_SECONDS; None if still busy."""
    deadline = time.monotonic() + TURN_WAIT_SECONDS
    while (
        lease := await coordinator.acquire_lock(key, ttl_seconds=210)
    ) is None and time.monotonic() < deadline:
        await asyncio.sleep(0.25)
    return lease


@dataclass(frozen=True)
class GitHubResponse:
    data: Any
    request_id: str | None
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    raw: bytes = field(default=b"", repr=False)


def classify_status(
    status: int, headers: dict, message: str = ""
) -> tuple[ExecutionErrorCode, bool, float | None]:
    limited = status == 429 or (
        status == 403
        and (
            headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in headers
            or "rate limit" in message.lower()
            or "abuse detection" in message.lower()
        )
    )
    if limited:
        delay = retry_after_seconds(headers.get("retry-after"))
        if delay is None and headers.get("x-ratelimit-remaining") == "0":
            try:
                delay = max(1.0, float(headers.get("x-ratelimit-reset", "0")) - time.time())
            except ValueError:
                delay = None
        return "rate_limited", True, delay or 60.0
    if status == 401:
        return "authentication_error", False, None
    if status == 403:
        return "authorization_error", False, None
    if status == 404:
        return "resource_not_found", False, None
    if status in {408, 425, 500, 502, 503, 504}:
        return "temporary_provider_error", True, retry_after_seconds(headers.get("retry-after"))
    return "validation_error", False, None


class GitHubHTTPClient:
    def __init__(self, coordinator_factory=None, client_factory=None):
        self._coordinator_factory = coordinator_factory or RedisCoordinator.from_settings
        self._client_factory = client_factory or httpx.AsyncClient
        self._active_client: ContextVar[Any] = ContextVar("github_http_client", default=None)

    @asynccontextmanager
    async def execution_session(self):
        """Reuse connections within one action; never share headers or credentials."""
        async with self._client_factory(timeout=15, follow_redirects=False) as client:
            token = self._active_client.set(client)
            try:
                yield
            finally:
                self._active_client.reset(token)

    @asynccontextmanager
    async def connection(self):
        active = self._active_client.get()
        if active is not None:
            yield active
        else:
            async with self._client_factory(timeout=15, follow_redirects=False) as client:
                yield client

    async def request(
        self,
        *,
        method,
        url,
        credential,
        json_body=None,
        headers=None,
        provider="GitHub",
        binary=False,
        data=None,
        max_bytes=2_000_000,
    ):
        del provider
        host = urlsplit(url).hostname
        if urlsplit(url).scheme != "https" or host not in TRUSTED_DOWNLOAD_HOSTS:
            raise ProviderTransportError(
                code="policy_blocked",
                retryable=False,
                safe_message="GitHub destination is outside approved API hosts.",
            )
        identity = (
            account_context.get()
            or hashlib.sha256((credential or "public").encode()).hexdigest()[:24]
        )
        coordinator = self._coordinator_factory()
        lease = None
        key = f"github:{identity}"
        request_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Audoryn-GitHub/2",
            "X-GitHub-Api-Version": settings.github_api_version,
            **(headers or {}),
        }
        if credential:
            request_headers["Authorization"] = "Bearer " + credential
        try:
            lease = await _wait_for_turn(coordinator, key + ":http")
            if lease is None:
                raise ProviderTransportError(
                    code="rate_limited",
                    retryable=True,
                    retry_after_seconds=1,
                    safe_message="This GitHub installation has a request in progress; queued work will wait.",
                )
            await _pace(coordinator, key, method)
            async with self.connection() as client, asyncio.timeout(90):
                for _ in range(4):
                    async with client.stream(
                        method, url, headers=request_headers, json=json_body, content=data
                    ) as response:
                        response_headers = dict(response.headers)
                        if response.status_code in {301, 302, 303, 307, 308}:
                            target, target_host = _redirect_target(response, binary)
                            if target_host != host:
                                request_headers.pop("Authorization", None)
                            url, host = target, target_host
                            continue
                        raw = await _read_body(response, max_bytes)
                        if response.status_code >= 400:
                            await self._raise_for_status(
                                coordinator, key, response.status_code, response_headers, raw
                            )
                        await coordinator.cache_delete(key + ":failures")
                        decoded = {} if not raw or binary else json.loads(raw)
                        return GitHubResponse(
                            decoded,
                            response.headers.get("x-github-request-id"),
                            response.status_code,
                            response_headers,
                            bytes(raw) if binary else b"",
                        )
                raise ProviderTransportError(
                    code="validation_error",
                    retryable=False,
                    safe_message="GitHub redirect budget exceeded.",
                )
        except (httpx.RequestError, ValueError, TimeoutError) as error:
            await self._failed(coordinator, key)
            raise ProviderTransportError(
                code="provider_unavailable",
                retryable=True,
                safe_message="GitHub returned no usable response; this account will retry within its budget.",
            ) from error
        except RedisError as error:
            raise ProviderTransportError(
                code="provider_unavailable",
                retryable=True,
                safe_message="GitHub account coordination is temporarily unavailable.",
            ) from error
        finally:
            try:
                if lease:
                    await coordinator.release_lock(lease)
                await coordinator.close()
            except RedisError:
                pass

    async def _raise_for_status(self, coordinator, key, status_code, response_headers, raw):
        """Raise the classified error for a failed response, cooling the installation down when GitHub
        limits it and counting a transient failure."""
        code, retryable, delay = classify_status(
            status_code, response_headers, bytes(raw).decode(errors="replace")[:1000]
        )
        if code == "rate_limited":
            await coordinator.cache_set(
                key + ":cooldown",
                str(time.time() + (delay or 60)),
                ttl_seconds=max(1, int(delay or 60)),
            )
        if code == "temporary_provider_error":
            await self._failed(coordinator, key)
        raise ProviderTransportError(
            code=code,
            retryable=retryable,
            retry_after_seconds=delay,
            safe_message=(
                "GitHub is limiting requests right now."
                if code == "rate_limited"
                else f"GitHub request stopped: {code.replace('_', ' ')}."
            ),
        )

    async def _failed(self, coordinator, key):
        count = int(await coordinator.cache_get(key + ":failures") or "0") + 1
        await coordinator.cache_set(key + ":failures", str(count), ttl_seconds=600)
        if count >= 5:
            await coordinator.cache_set(key + ":cooldown", str(time.time() + 120), ttl_seconds=120)
