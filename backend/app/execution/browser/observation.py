from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from app.execution.browser.contracts import (
    BrowserElement,
    BrowserForm,
    BrowserFrame,
    BrowserObservation,
)

MAX_VISIBLE_TEXT = 24_000
MAX_DOM_SNAPSHOT = 32_000
MAX_ARIA_SNAPSHOT = 24_000
MAX_ELEMENTS = 300

_SENSITIVE_INPUT_TYPES = {"password", "hidden"}
_SECRET_NAME = re.compile(r"(password|passwd|token|secret|api.?key|authorization)", re.IGNORECASE)


def _origin(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return None
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def _safe_value(*, element_type: str | None, name: str | None, value: object) -> str | None:
    if value is None:
        return None
    if (element_type or "").lower() in _SENSITIVE_INPUT_TYPES:
        return "[REDACTED]"
    if name and _SECRET_NAME.search(name):
        return "[REDACTED]"
    return str(value)[:500]


def _redact_text(value: str, sensitive_values: tuple[str, ...]) -> str:
    redacted = value
    for sensitive in sorted((item for item in sensitive_values if item), key=len, reverse=True):
        redacted = redacted.replace(sensitive, "[REDACTED]")
    return redacted


async def observe_page(
    page: Page,
    *,
    session_id: UUID,
    sensitive_values: tuple[str, ...] = (),
) -> BrowserObservation:
    title = _redact_text(await page.title(), sensitive_values)
    visible_text = _redact_text(
        (await page.locator("body").inner_text(timeout=5_000))[:MAX_VISIBLE_TEXT],
        sensitive_values,
    )

    raw_elements = await page.locator(
        "a,button,input,textarea,select,[role],[contenteditable='true']"
    ).evaluate_all(
        """(nodes) => nodes.slice(0, 300).map((el, index) => {
          const style = window.getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          const visible = style.visibility !== 'hidden' && style.display !== 'none'
            && rect.width >= 0 && rect.height >= 0;
          if (!visible) return null;
          const tag = el.tagName.toLowerCase();
          const explicitRole = el.getAttribute('role');
          const semanticRole = explicitRole
            || (tag === 'a' ? 'link'
            : tag === 'button' ? 'button'
            : tag === 'textarea' ? 'textbox'
            : tag === 'select' ? 'combobox'
            : tag === 'input' && el.type === 'checkbox' ? 'checkbox'
            : tag === 'input' && el.type === 'radio' ? 'radio'
            : tag === 'input' ? 'textbox'
            : null);
          const label = el.getAttribute('aria-label')
            || (el.labels && el.labels.length ? el.labels[0].innerText : null)
            || el.getAttribute('title')
            || el.getAttribute('name')
            || null;
          return {
            index,
            tag,
            role: semanticRole,
            name: label,
            text: (el.innerText || el.textContent || '').trim().slice(0, 500),
            elementType: el.getAttribute('type'),
            value: 'value' in el ? el.value : null,
            checked: 'checked' in el ? Boolean(el.checked) : null,
            selected: 'selected' in el ? Boolean(el.selected) : null,
            disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
            href: el.href || null
          };
        }).filter(Boolean)"""
    )

    discovered_sensitive_values: list[str] = []
    elements: list[BrowserElement] = []
    for position, item in enumerate(raw_elements[:MAX_ELEMENTS], start=1):
        if not isinstance(item, dict):
            continue
        name = str(item["name"]) if item.get("name") is not None else None
        element_type = str(item["elementType"]) if item.get("elementType") is not None else None
        raw_value = item.get("value")
        if raw_value not in (None, "") and (
            (element_type or "").lower() in _SENSITIVE_INPUT_TYPES
            or (name is not None and _SECRET_NAME.search(name))
        ):
            discovered_sensitive_values.append(str(raw_value))
        elements.append(
            BrowserElement(
                ref=f"e{int(item.get('index', position - 1)) + 1}",
                tag=str(item.get("tag") or ""),
                role=str(item["role"]) if item.get("role") is not None else None,
                name=_redact_text(name, sensitive_values) if name is not None else None,
                text=_redact_text(str(item.get("text") or ""), sensitive_values),
                element_type=element_type,
                value=_redact_text(
                    _safe_value(
                        element_type=element_type,
                        name=name,
                        value=item.get("value"),
                    )
                    or "",
                    sensitive_values,
                )
                if item.get("value") is not None
                else None,
                checked=item.get("checked") if isinstance(item.get("checked"), bool) else None,
                selected=item.get("selected") if isinstance(item.get("selected"), bool) else None,
                disabled=item.get("disabled") is True,
                href=(
                    _redact_text(str(item["href"]), sensitive_values)
                    if item.get("href") is not None
                    else None
                ),
            )
        )

    body = page.locator("body")
    try:
        aria_snapshot = (await body.aria_snapshot(timeout=5_000))[:MAX_ARIA_SNAPSHOT]
    except PlaywrightError:
        aria_snapshot = ""
    all_sensitive_values = tuple(
        dict.fromkeys((*sensitive_values, *tuple(discovered_sensitive_values)))
    )
    aria_snapshot = _redact_text(aria_snapshot, all_sensitive_values)

    dom_snapshot = await page.locator("body").evaluate(
        """(body) => {
          const clone = body.cloneNode(true);
          clone.querySelectorAll('script,style,noscript,template').forEach(node => node.remove());
          clone.querySelectorAll('input,textarea').forEach(node => {
            const type = (node.getAttribute('type') || '').toLowerCase();
            const name = node.getAttribute('name') || node.getAttribute('aria-label') || '';
            if (type === 'password' || /password|passwd|token|secret|api.?key|authorization/i.test(name)) {
              node.setAttribute('value', '[REDACTED]');
              node.textContent = '';
            } else if (node.hasAttribute('value')) {
              node.setAttribute('value', String(node.value || '').slice(0, 500));
            }
          });
          return clone.innerHTML;
        }"""
    )

    dom_snapshot = _redact_text(str(dom_snapshot), all_sensitive_values)

    raw_forms = await page.locator("form").evaluate_all(
        """(forms) => {
          const interactive = Array.from(
            document.querySelectorAll("a,button,input,textarea,select,[role],[contenteditable='true']")
          );
          return forms.slice(0, 100).map((form, index) => {
            const fields = Array.from(
              form.querySelectorAll("input,textarea,select,[contenteditable='true']")
            );
            const submits = Array.from(
              form.querySelectorAll("button[type='submit'],input[type='submit'],button:not([type])")
            );
            const refs = (nodes) => nodes
              .map((node) => interactive.indexOf(node))
              .filter((position) => position >= 0)
              .map((position) => "e" + String(position + 1));
            return {
              ref: "f" + String(index + 1),
              action: form.action || null,
              method: (form.method || 'get').toLowerCase(),
              fieldRefs: refs(fields),
              submitRefs: refs(submits)
            };
          });
        }"""
    )
    form_details = tuple(
        BrowserForm(
            ref=str(item.get("ref") or ""),
            action=(
                _redact_text(str(item["action"]), all_sensitive_values)
                if item.get("action") is not None
                else None
            ),
            method=str(item.get("method") or "get"),
            field_refs=tuple(str(ref) for ref in item.get("fieldRefs", [])),
            submit_refs=tuple(str(ref) for ref in item.get("submitRefs", [])),
        )
        for item in raw_forms
        if isinstance(item, dict)
    )

    frames = tuple(
        BrowserFrame(
            name=frame.name or None,
            url=_redact_text(frame.url, all_sensitive_values),
            origin=_origin(frame.url),
            is_main=frame == page.main_frame,
        )
        for frame in page.frames
    )
    forms = tuple(
        f"form:{index + 1}" for index in range(min(await page.locator("form").count(), 100))
    )
    links = tuple(item.ref for item in elements if item.role == "link")
    buttons = tuple(item.ref for item in elements if item.role == "button")
    inputs = tuple(
        item.ref for item in elements if item.role in {"textbox", "checkbox", "radio", "combobox"}
    )

    return BrowserObservation(
        id=f"obs_{uuid4().hex}",
        session_id=session_id,
        url=_redact_text(page.url, all_sensitive_values),
        title=title[:1_000],
        visible_text=visible_text,
        aria_snapshot=aria_snapshot,
        dom_snapshot=dom_snapshot[:MAX_DOM_SNAPSHOT],
        elements=tuple(elements),
        forms=forms,
        form_details=form_details,
        links=links,
        buttons=buttons,
        inputs=inputs,
        frames=frames,
        page_state={
            "loadState": "complete",
            "interactiveElementCount": len(elements),
            "formCount": len(forms),
            "frameCount": len(frames),
        },
        observed_at=datetime.now(UTC),
    )


def origin_for_url(url: str) -> str | None:
    return _origin(url)
