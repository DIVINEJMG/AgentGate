"""Console reads configuration.

Off unless explicitly enabled with a complete, valid key. An invalid configuration never
stops AgentGate from starting: the feature stays off and the problem is logged by name only.
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass, field

from pydantic import SecretStr

from app.application.console_reads.signing import ServiceKey

logger = logging.getLogger("audoryn.console_reads")

KNOWN_SCOPES = frozenset({"visibility.read", "observability.read", "security.read", "notifications.read"})


@dataclass(frozen=True)
class ConsoleReadsConfig:
    enabled: bool
    audience: str = "agentgate"
    keys: dict[str, ServiceKey] = field(default_factory=dict, repr=False)
    clock_skew_seconds: int = 60
    rate_per_minute: int = 120
    statement_timeout_ms: int = 3000
    problem: str | None = None


DISABLED = ConsoleReadsConfig(enabled=False)


def decode_secret(value: str) -> bytes:
    """Base64url (padding optional); at least 32 bytes."""
    text = value.strip()
    try:
        secret = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("secret is not base64url") from exc
    if len(secret) < 32:
        raise ValueError("secret is shorter than 32 bytes")
    return secret


def _key(prefix: str, key_id: str | None, secret: SecretStr | None, scopes: str, audience: str) -> ServiceKey | None:
    if secret is not None and not secret.get_secret_value().strip():
        secret = None  # an empty placeholder copied from .env.example
    if not key_id and secret is None and not scopes.strip():
        return None
    if not key_id or secret is None or not scopes.strip():
        raise ValueError(f"{prefix} needs an ID, a secret and scopes")
    scope_set = frozenset(part.strip() for part in scopes.split(",") if part.strip())
    unknown = scope_set - KNOWN_SCOPES
    if unknown:
        raise ValueError(f"{prefix} has unknown scopes: {', '.join(sorted(unknown))}")
    return ServiceKey(key_id=key_id, secret=decode_secret(secret.get_secret_value()), audience=audience, scopes=scope_set)


def load_config(settings: object) -> ConsoleReadsConfig:
    if not getattr(settings, "console_reads_enabled", False):
        return DISABLED
    try:
        audience = str(getattr(settings, "console_reads_audience", "agentgate"))
        skew = int(getattr(settings, "console_reads_clock_skew_seconds", 60))
        rate = int(getattr(settings, "console_reads_rate_per_minute", 120))
        timeout_ms = int(getattr(settings, "console_reads_statement_timeout_ms", 3000))
        if not 1 <= skew <= 300:
            raise ValueError("CONSOLE_READS_CLOCK_SKEW_SECONDS must be 1-300")
        if not 1 <= rate <= 6000:
            raise ValueError("CONSOLE_READS_RATE_PER_MINUTE must be 1-6000")
        if not 100 <= timeout_ms <= 30000:
            raise ValueError("CONSOLE_READS_STATEMENT_TIMEOUT_MS must be 100-30000")
        keys = [
            _key(
                "CONSOLE_READS_KEY",
                getattr(settings, "console_reads_key_id", None),
                getattr(settings, "console_reads_key_secret", None),
                str(getattr(settings, "console_reads_key_scopes", "") or ""),
                audience,
            ),
            _key(
                "CONSOLE_READS_PREVIOUS_KEY",
                getattr(settings, "console_reads_previous_key_id", None),
                getattr(settings, "console_reads_previous_key_secret", None),
                str(getattr(settings, "console_reads_previous_key_scopes", "") or ""),
                audience,
            ),
        ]
        active = {key.key_id: key for key in keys if key is not None}
        if not active:
            raise ValueError("CONSOLE_READS_KEY_ID, _SECRET and _SCOPES are required")
        if len(active) != len([key for key in keys if key is not None]):
            raise ValueError("current and previous key IDs must differ")
    except ValueError as exc:
        logger.error("console_reads.disabled_invalid_configuration problem=%s", exc, extra={"problem": str(exc)})
        return ConsoleReadsConfig(enabled=False, problem=str(exc))
    return ConsoleReadsConfig(
        enabled=True,
        audience=audience,
        keys=active,
        clock_skew_seconds=skew,
        rate_per_minute=rate,
        statement_timeout_ms=timeout_ms,
    )
