from __future__ import annotations

from app.execution.redaction import SENSITIVE_VALUE_PATTERN

SENSITIVE_AUTOCOMPLETE = {
    "current-password",
    "new-password",
    "one-time-code",
    "cc-number",
    "cc-csc",
}


def is_sensitive_field_metadata(
    *,
    element_type: str | None = None,
    name: str | None = None,
    element_id: str | None = None,
    label: str | None = None,
    placeholder: str | None = None,
    autocomplete: str | None = None,
) -> bool:
    if (element_type or "").lower() in {"password", "hidden"}:
        return True
    if (autocomplete or "").lower() in SENSITIVE_AUTOCOMPLETE:
        return True
    return any(
        value is not None and SENSITIVE_VALUE_PATTERN.search(value)
        for value in (name, element_id, label, placeholder)
    )
