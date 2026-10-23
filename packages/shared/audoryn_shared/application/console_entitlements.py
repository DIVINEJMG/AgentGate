"""The Console's entitlement channel (commercial plan, phase 3): signed requests that deliver
plans to AgentGate and read back what it applied.

Separate from the signed reads by design: its own signature label (so a read signature can
never authorise a write), its own key and settings, its own replay and rate-limit records in
Redis, and fixed scopes. Off unless enabled with a valid key; an invalid configuration never stops
AgentGate from starting, it leaves the channel off and is logged by name only.

Signing (``audoryn-entitlements-v1``): HMAC-SHA256 over the label, key id, audience, scope,
method, target (path and query as sent), timestamp, nonce and the SHA-256 of the body, one per
line. The shared vector in ``docs/integration/console-entitlements/signing-vector.json`` proves
both sides agree.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from audoryn_shared.application.console_reads.config import decode_secret
from audoryn_shared.application.console_reads.signing import (
    ReplayStoreUnavailable,
    ServiceKey,
    ServiceRequestRejected,
)

logger = logging.getLogger("audoryn.console_entitlements")

SIGNATURE_VERSION = "audoryn-entitlements-v1"
HEADER_PREFIX = "x-audoryn-service-"
WRITE_SCOPE, READ_SCOPE = "entitlements.write", "entitlements.read"
SCOPES = frozenset({WRITE_SCOPE, READ_SCOPE})
MAX_BODY_BYTES = 64 * 1024
_NONCE = re.compile(r"[a-f0-9]{32}")
_SIGNATURE = re.compile(r"v1=[a-f0-9]{64}")
_TIMESTAMP = re.compile(r"[0-9]{1,12}")


@dataclass(frozen=True)
class EntitlementsChannelConfig:
    enabled: bool
    audience: str = "agentgate"
    keys: dict[str, ServiceKey] = field(default_factory=dict, repr=False)
    clock_skew_seconds: int = 60
    rate_per_minute: int = 120
    problem: str | None = None


DISABLED = EntitlementsChannelConfig(enabled=False)


def _key(name: str, key_id: str | None, secret: SecretStr | None, audience: str) -> ServiceKey | None:
    value = secret.get_secret_value().strip() if secret is not None else ""
    if not key_id and not value:
        return None
    if not key_id or not value:
        raise ValueError(f"{name} needs an ID and a secret")
    return ServiceKey(key_id=key_id, secret=decode_secret(value), audience=audience, scopes=SCOPES)


def load_config(settings: Any) -> EntitlementsChannelConfig:
    if not settings.console_entitlements_enabled:
        return DISABLED
    try:
        audience = str(settings.console_entitlements_audience)
        skew, rate = int(settings.console_entitlements_clock_skew_seconds), int(settings.console_entitlements_rate_per_minute)
        if not 1 <= skew <= 300:
            raise ValueError("CONSOLE_ENTITLEMENTS_CLOCK_SKEW_SECONDS must be 1-300")
        if not 1 <= rate <= 6000:
            raise ValueError("CONSOLE_ENTITLEMENTS_RATE_PER_MINUTE must be 1-6000")
        keys = [key for key in (
            _key("CONSOLE_ENTITLEMENTS_KEY", settings.console_entitlements_key_id,
                 settings.console_entitlements_key_secret, audience),
            _key("CONSOLE_ENTITLEMENTS_PREVIOUS_KEY", settings.console_entitlements_previous_key_id,
                 settings.console_entitlements_previous_key_secret, audience),
        ) if key is not None]
        if not keys:
            raise ValueError("CONSOLE_ENTITLEMENTS_KEY_ID and _SECRET are required")
        if len({key.key_id for key in keys}) != len(keys):
            raise ValueError("current and previous key IDs must differ")
    except ValueError as exc:
        logger.error("console_entitlements.disabled_invalid_configuration problem=%s", exc)
        return EntitlementsChannelConfig(enabled=False, problem=str(exc))
    return EntitlementsChannelConfig(enabled=True, audience=audience, keys={key.key_id: key for key in keys},
                                     clock_skew_seconds=skew, rate_per_minute=rate)


def canonical_bytes(*, key_id: str, audience: str, scope: str, method: str, target: str, timestamp: str,
                    nonce: str, body: bytes, label: str = SIGNATURE_VERSION) -> bytes:
    """One line per field; `label` keeps each channel's signatures its own (billing: audoryn-billing-v1)."""
    fields = (label, key_id, audience, scope, method, target, timestamp, nonce,
              hashlib.sha256(body).hexdigest())
    return "\n".join(fields).encode("ascii")


def sign(key: ServiceKey, *, scope: str, method: str, target: str, timestamp: str, nonce: str, body: bytes,
         label: str = SIGNATURE_VERSION) -> str:
    """A request's signature, as the Console makes it (also used by tests and local tooling)."""
    canonical = canonical_bytes(key_id=key.key_id, audience=key.audience, scope=scope, method=method,
                                target=target, timestamp=timestamp, nonce=nonce, body=body, label=label)
    return "v1=" + hmac.new(key.secret, canonical, hashlib.sha256).hexdigest()


def _service_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in headers:
        normalized = name.lower()
        if normalized.startswith(HEADER_PREFIX):
            if normalized in values:
                raise ServiceRequestRejected("duplicate_header")
            values[normalized] = value
    return values


async def verify_request(*, config: EntitlementsChannelConfig, scope: str, method: str, target: str, body: bytes,
                         headers: Iterable[tuple[str, str]], guard: ChannelGuard, now: int,
                         label: str = SIGNATURE_VERSION) -> ServiceKey:
    """The verified key, or ServiceRequestRejected. `scope` comes from the matched route, never
    from the caller. `label` is the channel's signature label (audoryn_shared.application.internal_calls
    uses its own)."""
    if len(body) > MAX_BODY_BYTES or not target.startswith("/") or target.startswith("//"):
        raise ServiceRequestRejected("unsupported_request")
    values = _service_headers(headers)
    header = lambda name: values.get(HEADER_PREFIX + name, "")
    key = config.keys.get(header("key"))
    if key is None:
        raise ServiceRequestRejected("unknown_key")
    if header("audience") != config.audience:
        raise ServiceRequestRejected("wrong_audience")
    if header("scope") != scope:
        raise ServiceRequestRejected("wrong_scope")
    timestamp, nonce, signature = header("timestamp"), header("nonce"), header("signature")
    if not _TIMESTAMP.fullmatch(timestamp) or not _NONCE.fullmatch(nonce) or not _SIGNATURE.fullmatch(signature):
        raise ServiceRequestRejected("malformed_header")
    if abs(now - int(timestamp)) > config.clock_skew_seconds:
        raise ServiceRequestRejected("expired")
    expected = sign(key, scope=scope, method=method, target=target, timestamp=timestamp, nonce=nonce, body=body,
                    label=label)
    if not hmac.compare_digest(signature, expected):
        raise ServiceRequestRejected("bad_signature")
    # Claimed only after the signature checks out, so forged requests cannot burn nonces.
    if not await guard.claim(key_id=key.key_id, nonce=nonce, ttl_seconds=2 * config.clock_skew_seconds + 1):
        raise ServiceRequestRejected("replayed")
    return key


class ChannelGuard:
    """A channel's replay and rate-limit records in Redis, under its own namespace. Any Redis
    failure fails closed."""

    def __init__(self, client: Any, namespace: str = "console-entitlements") -> None:
        self._client, self._namespace = client, namespace

    @property
    def client(self) -> Any:
        """The Redis client, which also holds the gateway's traffic limits (app.application.gateway_limits)."""
        return self._client

    async def claim(self, *, key_id: str, nonce: str, ttl_seconds: int) -> bool:
        try:
            return bool(await self._client.set(f"{self._namespace}:replay:{key_id}:{nonce}", "1",
                                               nx=True, ex=ttl_seconds))
        except Exception as exc:
            raise ReplayStoreUnavailable(type(exc).__name__) from exc

    async def wait_seconds(self, *, key_id: str, limit: int, now: int) -> int | None:
        """Count one request; the seconds to wait when over the limit, else None."""
        window = now // 60
        key = f"{self._namespace}:rate:{key_id}:{window}"
        try:
            count = int(await self._client.incr(key))
            if count == 1:
                await self._client.expire(key, 120)
        except Exception as exc:
            raise ReplayStoreUnavailable(type(exc).__name__) from exc
        return None if count <= limit else max(1, (window + 1) * 60 - now)
