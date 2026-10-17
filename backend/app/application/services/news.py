"""The workspace app's "What's new" (plans, news and upgrade prompts plan, part 3).

Two sources, merged in the app: the CMS changelog (served by the website server from its verified
release) and the Console's announcements for the workspace app, read here over the billing
channel (`content.read`), cached briefly, checked, and filtered by the workspace's plan and the
person's role. Read and dismissed state is the person's own (`content_state`).
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.application.console_billing import BillingUnavailable, ConsoleBilling

CACHE_SECONDS = 60.0
# Keys of "What's new" entries in the person's read state.
PREFIX = "news:"
KEY = re.compile(r"^news:(changelog|announcement):[A-Za-z0-9-]{1,64}$")
_SEVERITIES = frozenset({"info", "success", "warning", "critical"})
_cache: tuple[float, list[dict[str, Any]]] | None = None


def forget() -> None:
    global _cache
    _cache = None


def _link(value: object) -> str | None:
    """A button's address: a page in the app (`/app/...`) or an HTTPS address."""
    if not isinstance(value, str) or not value or len(value) > 1000:
        return None
    if value.startswith("/app/") or value == "/app":
        return value
    target = urlparse(value)
    return value if target.scheme == "https" and target.hostname else None


def _clean(item: Any) -> dict[str, Any] | None:
    """An announcement as the app may show it, or None when the Console's answer is not usable."""
    if not isinstance(item, dict):
        return None
    try:
        identifier = str(UUID(str(item.get("id"))))
    except ValueError:
        return None
    title, body = item.get("title"), item.get("body")
    if not isinstance(title, str) or not isinstance(body, str) or not title.strip() or len(title) > 200 \
            or len(body) > 10_000 or not isinstance(item.get("publishedAt"), str):
        return None
    codes, roles = item.get("planCodes") or [], item.get("roles") or []
    if not isinstance(codes, list) or not isinstance(roles, list):
        return None
    label = item.get("ctaLabel")
    link = _link(item.get("ctaUrl"))
    return {
        "id": identifier, "kind": str(item.get("kind") or "notice")[:48], "title": title.strip(), "body": body,
        "severity": item.get("severity") if item.get("severity") in _SEVERITIES else "info",
        "publishedAt": item["publishedAt"], "endsAt": item.get("endsAt") if isinstance(item.get("endsAt"), str) else None,
        "cta": {"label": label[:120], "url": link} if isinstance(label, str) and label.strip() and link else None,
        "dismissible": item.get("dismissible") is not False, "major": item.get("major") is True,
        "upgrade": item.get("upgrade") is True,
        "planCodes": [str(code) for code in codes], "roles": [str(role) for role in roles],
    }


async def announcements(billing: ConsoleBilling | None) -> list[dict[str, Any]]:
    """Every announcement for the workspace app; the last good answer while the Console is away."""
    global _cache
    if billing is None:
        return []
    if _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    try:
        answer = await billing.content()
    except BillingUnavailable:
        return _cache[1] if _cache is not None else []
    raw = answer.get("announcements")
    items = [item for item in map(_clean, raw if isinstance(raw, list) else []) if item is not None]
    _cache = (time.monotonic(), items)
    return items


def for_person(items: list[dict[str, Any]], *, plan_code: str | None, role: str) -> list[dict[str, Any]]:
    """The announcements this person sees: everyone's, unless limited to plans or roles."""
    return [{key: value for key, value in item.items() if key not in {"planCodes", "roles"}} for item in items
            if (not item["planCodes"] or plan_code in item["planCodes"]) and (not item["roles"] or role in item["roles"])]
