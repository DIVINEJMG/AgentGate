from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_VALUE_PATTERN = re.compile(
    r"(password|passwd|passcode|token|secret|api.?key|authorization|"
    r"access.?code|one.?time|otp|pin)",
    re.IGNORECASE,
)


_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|passcode|token|secret|api.?key|authorization|"
    r"access.?code|one.?time|otp|pin)\b(\s*[:=]\s*)([^&\s,;]+)"
)


def redact_inline_secrets(value: str) -> str:
    return _INLINE_SECRET_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        value,
    )


def redact_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.scheme and not parts.netloc and not parts.query:
        return redact_inline_secrets(value)

    query = urlencode(
        [
            (
                key,
                "[REDACTED]" if SENSITIVE_VALUE_PATTERN.search(key) else item,
            )
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ],
        doseq=True,
    )

    netloc = parts.netloc
    if parts.password is not None and parts.hostname is not None:
        host = parts.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port is not None else ""
        username = parts.username or ""
        netloc = f"{username}:[REDACTED]@{host}{port}"

    return redact_inline_secrets(
        urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    )


def redact_text(value: str, sensitive_values: Sequence[str] = ()) -> str:
    redacted = redact_inline_secrets(value)
    for sensitive in sorted(
        (item for item in sensitive_values if item),
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(sensitive, "[REDACTED]")
    return redacted


def _mapping_describes_sensitive_value(value: Mapping[str, object]) -> bool:
    locator = value.get("locator")
    if isinstance(locator, Mapping):
        locator_values = [str(locator.get(key) or "") for key in ("value", "name", "label", "text")]
        if any(SENSITIVE_VALUE_PATTERN.search(item) for item in locator_values if item):
            return True
    descriptor_values = [
        str(value.get(key) or "")
        for key in (
            "name",
            "field",
            "label",
            "id",
            "placeholder",
            "autocomplete",
            "credentialKey",
        )
    ]
    return any(SENSITIVE_VALUE_PATTERN.search(item) for item in descriptor_values if item)


def redact_sensitive_structure(value: object) -> object:
    """Redact secret-like values before persistence, logs, Audit, or realtime."""

    if isinstance(value, Mapping):
        sensitive_container = _mapping_describes_sensitive_value(value)
        result: dict[str, object] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if SENSITIVE_VALUE_PATTERN.search(key):
                result[key] = "[REDACTED]"
                continue
            if sensitive_container and key in {"value", "text", "content"}:
                result[key] = "[REDACTED]"
                continue
            result[key] = redact_sensitive_structure(item)
        return result
    if isinstance(value, tuple):
        return tuple(redact_sensitive_structure(item) for item in value)
    if isinstance(value, list):
        return [redact_sensitive_structure(item) for item in value]
    if isinstance(value, str):
        return redact_url(value)
    return value


def redacted_dict(value: Mapping[str, Any]) -> dict[str, object]:
    redacted = redact_sensitive_structure(value)
    assert isinstance(redacted, dict)
    return redacted
