from __future__ import annotations

import re

from app.execution.browser.sensitive import is_sensitive_field_metadata

# Inspect the live target as well as the observation: the page can change between
# planning and execution. Only metadata is returned; field values stay in-page.
KEYBOARD_METADATA_SCRIPT = """el => ({
  tag: el.tagName.toLowerCase(), type: el.type || '', role: el.getAttribute('role') || '',
  name: el.name || '', id: el.id || '', label: el.getAttribute('aria-label')
    || (el.labels?.length ? el.labels[0].innerText : '') || '',
  searchRegion: Boolean(el.closest('[role="search"]')),
  placeholder: el.getAttribute('placeholder') || '', autocomplete: el.autocomplete || '',
  formMethod: el.form ? (el.form.method || 'get').toLowerCase() : null,
  formFields: el.form ? Array.from(el.form.elements).map(field => ({
    type: field.type || '', name: field.name || '', id: field.id || '',
    autocomplete: field.autocomplete || ''
  })) : []
})"""


def read_only_search_enter(metadata: dict[str, object]) -> bool:
    if str(metadata.get("tag") or "") not in {"input", "textarea"}:
        return False
    if is_sensitive_field_metadata(
        element_type=str(metadata.get("type") or ""),
        name=str(metadata.get("name") or ""),
        element_id=str(metadata.get("id") or ""),
        autocomplete=str(metadata.get("autocomplete") or ""),
    ):
        return False
    search = (
        metadata.get("type") == "search"
        or metadata.get("role") == "searchbox"
        or metadata.get("searchRegion") is True
        or re.search(
            r"\b(search|find|filter)\b",
            " ".join(str(metadata.get(key) or "") for key in ("label", "name", "placeholder")),
            re.IGNORECASE,
        )
        is not None
    )
    if not search or metadata.get("formMethod") not in {None, "get"}:
        return False
    fields = metadata.get("formFields")
    return not any(
        is_sensitive_field_metadata(
            element_type=str(field.get("type") or ""),
            name=str(field.get("name") or ""),
            element_id=str(field.get("id") or ""),
            autocomplete=str(field.get("autocomplete") or ""),
        )
        for field in (fields if isinstance(fields, list) else [])
        if isinstance(field, dict)
    )


def read_only_key(metadata: dict[str, object], key: str) -> bool:
    normalized = key.lower()
    if normalized in {"enter", "numpadenter"}:
        return read_only_search_enter(metadata)
    if normalized in {
        "tab",
        "shift+tab",
        "escape",
        "arrowup",
        "arrowdown",
        "arrowleft",
        "arrowright",
        "home",
        "end",
        "pageup",
        "pagedown",
    }:
        return True
    return (
        str(metadata.get("tag") or "") in {"input", "textarea"}
        and (normalized in {"control+a", "meta+a", "backspace", "delete", "space"} or len(key) == 1)
        and str(metadata.get("type") or "text")
        not in {"button", "submit", "image", "reset", "file", "checkbox", "radio"}
        and not is_sensitive_field_metadata(
            element_type=str(metadata.get("type") or ""),
            name=str(metadata.get("name") or ""),
            autocomplete=str(metadata.get("autocomplete") or ""),
        )
    )
