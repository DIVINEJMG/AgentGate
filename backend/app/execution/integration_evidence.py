from __future__ import annotations

from urllib.parse import urlsplit


def external_references(output: object) -> list[dict[str, str]]:
    """Only typed adapter URL fields become references; prose is not parsed as proof."""
    references: dict[str, dict[str, str]] = {}
    values = output if isinstance(output, list) else [output]
    for value in values:
        if not isinstance(value, dict):
            continue
        url = value.get("webUrl")
        if not isinstance(url, str):
            continue
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            continue
        references[url] = {
            "url": url,
            "externalId": str(value.get("id") or value.get("number") or ""),
            "title": str(value.get("title") or value.get("name") or "Provider object")[:240],
        }
    return list(references.values())[:50]
