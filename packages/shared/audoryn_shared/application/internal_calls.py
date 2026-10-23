"""Signed calls between AgentGate's own services (architecture plan, Phase 3).

A process that does not run steps (SERVICE_ROLE=api) sends a due step straight to the execution
service instead of a QStash wake-up: quicker, and no QStash message per step. The call reaches the
same `/internal/v1/runtime/execute` handler as QStash, with the same step lease and slot claim, so
a step is never run twice. It is signed like the Console's channel (HMAC-SHA256 over the canonical
request, audoryn_shared.application.console_entitlements) under its own label, audience and Redis replay
records, so no other channel's signature can authorise it. When the call is refused or the
execution service can't be reached, the caller falls back to QStash.

Off unless INTERNAL_CALL_KEY_ID and INTERNAL_CALL_KEY_SECRET are set; the previous key is accepted
while a new one is rolled out to every process.
"""
from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

import httpx

from audoryn_shared.application.console_entitlements import (
    HEADER_PREFIX,
    ChannelGuard,
    EntitlementsChannelConfig,
    sign,
    verify_request,
)
from audoryn_shared.application.console_reads.config import decode_secret
from audoryn_shared.application.console_reads.signing import ServiceKey
from audoryn_shared.bootstrap.settings import settings

logger = logging.getLogger(__name__)

LABEL = "audoryn-internal-v1"
AUDIENCE = "agentgate-execution"
EXECUTE_SCOPE = "runtime.execute"
EXECUTE_PATH = "/internal/v1/runtime/execute"
CALL_TIMEOUT_SECONDS = 10.0


def load_config(source: Any) -> EntitlementsChannelConfig:
    """The keys, current first. An invalid key leaves the calls off (QStash carries every step)."""
    pairs = ((source.internal_call_key_id, source.internal_call_key_secret),
             (source.internal_call_previous_key_id, source.internal_call_previous_key_secret))
    try:
        keys = [ServiceKey(key_id=key_id, secret=decode_secret(secret.get_secret_value()), audience=AUDIENCE,
                           scopes=frozenset({EXECUTE_SCOPE}))
                for key_id, secret in pairs if key_id and secret is not None]
    except ValueError as error:
        logger.error("internal_calls.disabled_invalid_configuration problem=%s", error)
        return EntitlementsChannelConfig(enabled=False, problem=str(error))
    if not keys or len({key.key_id for key in keys}) != len(keys):
        return EntitlementsChannelConfig(enabled=False)
    return EntitlementsChannelConfig(enabled=True, audience=AUDIENCE, keys={key.key_id: key for key in keys})


@lru_cache
def _config() -> EntitlementsChannelConfig:
    return load_config(settings)


@lru_cache
def _guard() -> ChannelGuard:
    from redis.asyncio import Redis

    return ChannelGuard(Redis.from_url(settings.redis_dsn, decode_responses=True), namespace="internal-calls")


def signed_headers(config: EntitlementsChannelConfig, *, scope: str, method: str, target: str, body: bytes,
                   now: int | None = None) -> dict[str, str]:
    key = next(iter(config.keys.values()))
    timestamp, nonce = str(int(time.time()) if now is None else now), secrets.token_hex(16)
    signature = sign(key, scope=scope, method=method, target=target, timestamp=timestamp, nonce=nonce, body=body,
                     label=LABEL)
    return {HEADER_PREFIX + name: value for name, value in (
        ("key", key.key_id), ("audience", config.audience), ("scope", scope), ("timestamp", timestamp),
        ("nonce", nonce), ("signature", signature))}


def is_signed(headers: Iterable[tuple[str, str]]) -> bool:
    return any(name.lower().startswith(HEADER_PREFIX) for name, _ in headers)


async def verify(*, scope: str, method: str, target: str, body: bytes, headers: Iterable[tuple[str, str]],
                 config: EntitlementsChannelConfig | None = None, guard: ChannelGuard | None = None) -> None:
    """Raises ServiceRequestRejected (or ReplayStoreUnavailable) unless the call is ours and fresh."""
    from audoryn_shared.application.console_reads.signing import ServiceRequestRejected

    config = config or _config()
    if not config.enabled:
        raise ServiceRequestRejected("internal_calls_off")
    await verify_request(config=config, scope=scope, method=method, target=target, body=body, headers=headers,
                         guard=guard or _guard(), now=int(time.time()), label=LABEL)


async def call_execution(body: bytes) -> str | None:
    """Sends a due step to the execution service: its answer ("accepted" or "busy"), or None when
    QStash must carry it (calls off, refused, capacity full, or unreachable)."""
    if not settings.execution_call_url or not (config := _config()).enabled:
        return None
    headers = {"content-type": "application/json",
               **signed_headers(config, scope=EXECUTE_SCOPE, method="POST", target=EXECUTE_PATH, body=body)}
    try:
        async with httpx.AsyncClient(timeout=CALL_TIMEOUT_SECONDS) as client:
            response = await client.post(settings.execution_call_url.rstrip("/") + EXECUTE_PATH,
                                         content=body, headers=headers)
    except httpx.HTTPError as error:
        logger.warning("Execution service unreachable (%s); the step goes through QStash", type(error).__name__)
        return None
    answer = response.json().get("status") if response.status_code == 202 else None
    if answer not in ("accepted", "busy"):
        logger.warning("Execution service answered %s; the step goes through QStash", response.status_code)
        return None
    return answer
