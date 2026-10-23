"""Traffic limits (docs/plans/architecture-scale-plan.md, section 5.5).

Each request belongs to one route group, whose policy holds its rate rules and body cap. Rules count
per caller IP, per session (an HMAC fingerprint of the bearer token, never the token) and per
organization in the path, in fixed windows in Redis: one round trip runs every rule of the request.

Modes: `off` checks nothing; `report` counts and logs what *would* be refused but lets it through;
`enforce` refuses with 429 (rates) or 413 (bodies). Plan limits are not here: they stay with the
service that owns the data.

The Console sets the policies (Phase 1e): AgentGate keeps its document in Redis under
`<prefix>:policies`, and `Limiter.follow` reads it every few seconds. A group the document names
replaces that group's policy from the settings; its `mode`, when set, replaces GATEWAY_LIMITS_MODE.
An invalid document is ignored and a missing one changes nothing: the last good policies stay.

When Redis is unavailable, a fail-closed policy (sign-in) refuses with 503 under `enforce`, and every
other policy lets the request through, so an outage of the limiter never takes the product down.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("audoryn.gateway")
Mode = Literal["off", "report", "enforce"]
KeyKind = Literal["ip", "session", "organization"]
_ORGANIZATION = re.compile(r"/organizations/([0-9a-fA-F-]{36})(?:/|$)")
_AUTH = frozenset({"/api/v2/auth/login", "/api/v2/auth/signup", "/api/v2/auth/github/start",
                   "/api/v2/auth/github/exchange"})
_READS = frozenset({"GET", "HEAD", "OPTIONS"})
# Counts every rule's key in one round trip; returns the 1-based index of the first rule over its
# limit, or 0. KEYS: the window counters; ARGV: limit, window seconds, limit, window seconds, ...
_SCRIPT = """
local over = 0
for i, key in ipairs(KEYS) do
  local count = redis.call('INCR', key)
  if count == 1 then redis.call('EXPIRE', key, ARGV[i * 2]) end
  if over == 0 and count > tonumber(ARGV[i * 2 - 1]) then over = i end
end
return over
"""


class Rule(BaseModel):
    key: KeyKind
    limit: int = Field(ge=1)
    window_seconds: int = Field(ge=1, le=86_400)


class Policy(BaseModel):
    rules: list[Rule] = Field(default_factory=list)
    max_body_bytes: int | None = Field(default=None, ge=1)
    fail_closed: bool = False
    mode: Mode | None = None  # None follows GATEWAY_LIMITS_MODE


Group = Literal["auth", "upload", "write", "general", "realtime", "internal"]


class PolicyDocument(BaseModel):
    """The policies the Console set, as AgentGate keeps them in Redis."""
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    mode: Mode | None = None
    policies: dict[Group, Policy] = Field(default_factory=dict)


def _rules(*rules: tuple[KeyKind, int, int]) -> list[Rule]:
    return [Rule(key=key, limit=limit, window_seconds=window) for key, limit, window in rules]


MiB = 1024 * 1024
# Starting values. `report` mode shows real traffic against them before any is enforced.
DEFAULT_POLICIES: dict[str, Policy] = {
    "auth": Policy(rules=_rules(("ip", 30, 300)), max_body_bytes=64 * 1024, fail_closed=True),
    # An attachment is at most 10 MiB (conversation_attachment_max_bytes), about 13.4 MiB as base64 JSON.
    "upload": Policy(rules=_rules(("session", 60, 60), ("ip", 120, 60)), max_body_bytes=16 * MiB),
    "write": Policy(rules=_rules(("ip", 600, 60), ("session", 240, 60), ("organization", 1200, 60)),
                    max_body_bytes=2 * MiB),
    "general": Policy(rules=_rules(("ip", 1200, 60), ("session", 600, 60)), max_body_bytes=2 * MiB),
    "realtime": Policy(rules=_rules(("ip", 60, 60))),
    # QStash, the Console and provider webhooks: each is signed and checked by the service.
    "internal": Policy(max_body_bytes=8 * MiB),
}


def policy_for(method: str, path: str, websocket: bool = False) -> str | None:
    """The route group of a request; None for the health checks, which are never limited."""
    if websocket:
        return "realtime"
    if path.startswith(("/internal/", "/integration-events/")):
        return "internal"
    if path.startswith("/health/"):
        return None
    if method == "POST" and path in _AUTH:
        return "auth"
    if method == "POST" and path.endswith("/attachments"):
        return "upload"
    return "general" if method in _READS else "write"


@dataclass(frozen=True)
class Decision:
    policy: str
    mode: Mode
    over: Rule | None = None  # the first rule over its limit
    unavailable: bool = False  # Redis could not be asked
    fail_closed: bool = False

    @property
    def refuse(self) -> int | None:
        """The status to refuse with under `enforce`, or None to let the request through."""
        if self.mode != "enforce":
            return None
        if self.over is not None:
            return 429
        return 503 if self.unavailable and self.fail_closed else None

    def retry_after(self, now: float | None = None) -> int:
        if self.over is None:
            return 5
        window = self.over.window_seconds
        return max(1, window - int(now or time.time()) % window)


class Limiter:
    def __init__(self, redis: Redis | None, secret: bytes, policies: dict[str, Policy], mode: Mode,
                 prefix: str = "audoryn:gateway") -> None:
        self.redis, self._secret, self.policies, self._prefix = redis, secret, policies, prefix
        self.mode: Mode = mode
        self._settings_policies: dict[str, Policy] = policies
        self._settings_mode: Mode = mode
        self.console_version: int | None = None  # the Console document in use, if any
        self._refused: str | None = None
        self._script = redis.register_script(_SCRIPT) if redis is not None else None
        self._warned = 0.0

    async def follow(self, every_seconds: float) -> None:
        """Keeps the Console's policies in use, reading them every `every_seconds`."""
        while True:
            await self.refresh()
            await asyncio.sleep(every_seconds)

    async def refresh(self) -> None:
        if self.redis is None:
            return
        try:
            raw = await self.redis.get(f"{self._prefix}:policies")
        except (RedisError, OSError, TimeoutError):
            return  # keep the policies in use
        if raw is None or raw == self._refused:
            return
        try:
            document = PolicyDocument.model_validate_json(raw)
        except ValidationError as error:
            self._refused = raw  # warn once per document, not on every read
            logger.warning("Console traffic limits ignored: invalid document (%s errors); the last good stay",
                           error.error_count())
            return
        if document.version == self.console_version:
            return
        self.policies = {**self._settings_policies, **document.policies}
        self.mode = document.mode or self._settings_mode
        self.console_version = document.version
        logger.warning("Console traffic limits version %s in use (mode %s, %s groups set)",
                       document.version, self.mode, len(document.policies))

    def mode_of(self, policy: str | None) -> Mode:
        if policy is None or self.redis is None:
            return "off"
        return self.policies[policy].mode or self.mode

    def fingerprint(self, value: str) -> str:
        return hmac.new(self._secret, value.encode(), hashlib.sha256).hexdigest()[:32]

    def body_too_large(self, policy: str | None, size: int) -> bool:
        cap = self.policies[policy].max_body_bytes if policy else None
        return cap is not None and size > cap

    async def check(self, policy: str | None, *, ip: str | None, token: str | None, path: str) -> Decision:
        mode = self.mode_of(policy)
        if policy is None or mode == "off":
            return Decision(policy or "-", "off")
        found: dict[KeyKind, str | None] = {"ip": ip, "session": token, "organization": None}
        if match := _ORGANIZATION.search(path):
            found["organization"] = match.group(1).lower()
        now = int(time.time())
        rules = [rule for rule in self.policies[policy].rules if found[rule.key]]
        if not rules:
            return Decision(policy, mode)
        keys = [f"{self._prefix}:rate:{policy}:{rule.key}:{self.fingerprint(found[rule.key] or '')}:"
                f"{rule.window_seconds}:{now // rule.window_seconds}" for rule in rules]
        assert self._script is not None
        try:
            over = int(await self._script(keys=keys, args=[n for rule in rules for n in (rule.limit, rule.window_seconds + 1)]))
        except (RedisError, OSError, TimeoutError) as error:
            self._warn_unavailable(error)
            return Decision(policy, mode, unavailable=True, fail_closed=self.policies[policy].fail_closed)
        decision = Decision(policy, mode, over=rules[over - 1] if over else None)
        if decision.over is not None:
            logger.warning(json.dumps({"event": "gateway.limit", "mode": mode, "policy": policy, "path": path,
                                       "rule": f"{decision.over.key} {decision.over.limit}/{decision.over.window_seconds}s"},
                                      separators=(",", ":")))
        return decision

    def _warn_unavailable(self, error: Exception) -> None:
        if time.monotonic() - self._warned > 30:  # once per 30 s, not once per request
            self._warned = time.monotonic()
            logger.warning("Limits unavailable (%s): fail-closed policies refuse, the rest pass", type(error).__name__)
