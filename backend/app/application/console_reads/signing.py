"""Verification of signed Console read requests (audoryn-service-v1).

Byte-compatible with Audoryn Console's ``service_signing.py``; the shared test vector in
``docs/integration/console-reads/signing-vector.json`` proves both sides agree. Only GET
requests with an empty body exist in this contract. Rejection reasons are for logs only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

SIGNATURE_VERSION = "audoryn-service-v1"
HEADER_PREFIX = "x-audoryn-service-"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SCOPE = re.compile(r"[a-z][a-z0-9_.:-]{0,95}")
_NONCE = re.compile(r"[a-f0-9]{32}")
_SIGNATURE = re.compile(r"v1=[a-f0-9]{64}")
_TIMESTAMP = re.compile(r"[0-9]{1,12}")
_EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


class ServiceRequestRejected(ValueError):
    """Authentication failure. ``reason`` is a fixed internal code, never key material."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ReplayStoreUnavailable(RuntimeError):
    """The replay store could not answer; the request must be refused."""


@dataclass(frozen=True)
class ServiceKey:
    key_id: str
    secret: bytes = field(repr=False)
    audience: str
    scopes: frozenset[str]

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.key_id) or not _IDENTIFIER.fullmatch(self.audience):
            raise ValueError("Invalid service key identity or audience")
        if len(self.secret) < 32:
            raise ValueError("Service signing keys require at least 32 bytes")
        if not self.scopes or any(not _SCOPE.fullmatch(scope) for scope in self.scopes):
            raise ValueError("Explicit service scopes are required")


class ReplayProtection(Protocol):
    async def claim(self, *, key_id: str, nonce: str, ttl_seconds: int) -> bool:
        """Atomically claim a nonce; False means it was already used.

        Raise ReplayStoreUnavailable when the store cannot answer.
        """
        ...


def canonical_bytes(
    *, key_id: str, audience: str, scope: str, method: str, target: str, timestamp: str, nonce: str
) -> bytes:
    fields = (SIGNATURE_VERSION, key_id, audience, scope, method, target, timestamp, nonce, _EMPTY_BODY_SHA256)
    return "\n".join(fields).encode("ascii")


def sign(key: ServiceKey, *, scope: str, target: str, timestamp: str, nonce: str) -> str:
    """Signature for a GET request. Used by tests and local tooling, not by AgentGate itself."""
    digest = hmac.new(
        key.secret,
        canonical_bytes(
            key_id=key.key_id,
            audience=key.audience,
            scope=scope,
            method="GET",
            target=target,
            timestamp=timestamp,
            nonce=nonce,
        ),
        hashlib.sha256,
    ).hexdigest()
    return "v1=" + digest


def _valid_target(target: str) -> bool:
    return (
        target.startswith("/")
        and not target.startswith("//")
        and len(target) <= 2048
        and "#" not in target
        and "\\" not in target
        and all(33 <= ord(char) <= 126 for char in target)
    )


def _service_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in headers:
        normalized = name.lower()
        if not normalized.startswith(HEADER_PREFIX):
            continue
        if normalized in values:
            raise ServiceRequestRejected("duplicate_header")
        values[normalized] = value
    return values


async def verify_read_request(
    *,
    keys: Mapping[str, ServiceKey],
    audience: str,
    required_scope: str,
    method: str,
    target: str,
    body: bytes,
    headers: Iterable[tuple[str, str]],
    replay: ReplayProtection,
    now: int,
    max_clock_skew_seconds: int,
) -> ServiceKey:
    """Return the verified key or raise ServiceRequestRejected.

    ``target`` and ``required_scope`` must come from the request AgentGate actually
    received and the route it matched, never from caller-supplied headers.
    """
    if method != "GET" or body:
        raise ServiceRequestRejected("unsupported_request")
    if not _valid_target(target):
        raise ServiceRequestRejected("invalid_target")
    values = _service_headers(headers)
    key_id = values.get(HEADER_PREFIX + "key", "")
    key = keys.get(key_id)
    scope = values.get(HEADER_PREFIX + "scope", "")
    timestamp = values.get(HEADER_PREFIX + "timestamp", "")
    nonce = values.get(HEADER_PREFIX + "nonce", "")
    signature = values.get(HEADER_PREFIX + "signature", "")
    if key is None:
        raise ServiceRequestRejected("unknown_key")
    if key.audience != audience or values.get(HEADER_PREFIX + "audience", "") != audience:
        raise ServiceRequestRejected("wrong_audience")
    if scope != required_scope or scope not in key.scopes:
        raise ServiceRequestRejected("wrong_scope")
    if not _TIMESTAMP.fullmatch(timestamp) or not _NONCE.fullmatch(nonce) or not _SIGNATURE.fullmatch(signature):
        raise ServiceRequestRejected("malformed_header")
    if abs(now - int(timestamp)) > max_clock_skew_seconds:
        raise ServiceRequestRejected("expired")
    expected = sign(key, scope=scope, target=target, timestamp=timestamp, nonce=nonce)
    if not hmac.compare_digest(signature, expected):
        raise ServiceRequestRejected("bad_signature")
    # Claimed only after the signature checks out, so forged requests cannot burn nonces.
    if not await replay.claim(key_id=key_id, nonce=nonce, ttl_seconds=2 * max_clock_skew_seconds + 1):
        raise ServiceRequestRejected("replayed")
    return key
