from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from app.execution.browser.egress import evaluate_browser_egress
from app.execution.browser.policy import BrowserDomainPolicy, BrowserNavigationBlocked
from app.execution.redaction import redact_text, redact_url

MAX_HTTP_TEXT = 12_000
MAX_HTTP_LINKS = 120
MAX_HTTP_FORMS = 20
MAX_HTTP_FIELDS_PER_FORM = 40
MAX_HTTP_REDIRECTS = 5


@dataclass(frozen=True, slots=True)
class HttpPageObservation:
    url: str
    status_code: int
    content_type: str
    title: str
    visible_text: str
    links: tuple[dict[str, str], ...]
    forms: tuple[dict[str, object], ...]
    truncated: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "statusCode": self.status_code,
            "contentType": self.content_type,
            "title": self.title,
            "visibleText": self.visible_text,
            "links": list(self.links),
            "forms": list(self.forms),
            "truncated": self.truncated,
            "transport": "http",
        }


class _BoundedHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._text_chars = 0
        self.links: list[dict[str, str]] = []
        self.forms: list[dict[str, object]] = []
        self._current_form: dict[str, object] | None = None

    @property
    def title(self) -> str:
        return " ".join(" ".join(self._title_parts).split())[:1000]

    @property
    def visible_text(self) -> str:
        return "\n".join(self._text_parts)[:MAX_HTTP_TEXT]

    def _append_text(self, value: str) -> None:
        if self._text_chars >= MAX_HTTP_TEXT:
            return
        clean = " ".join(value.split())
        if not clean:
            return
        remaining = MAX_HTTP_TEXT - self._text_chars
        piece = clean[:remaining]
        self._text_parts.append(piece)
        self._text_chars += len(piece) + 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).lower(): (value or "") for key, value in attrs}
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
            return
        if lowered == "title":
            self._in_title = True
        if lowered == "a" and len(self.links) < MAX_HTTP_LINKS:
            href = values.get("href", "").strip()
            if href:
                label = values.get("aria-label") or values.get("title") or ""
                self.links.append({"href": href, "text": label[:300]})
        if lowered == "form" and len(self.forms) < MAX_HTTP_FORMS:
            self._current_form = {
                "action": values.get("action", ""),
                "method": (values.get("method") or "get").lower(),
                "fields": [],
            }
            self.forms.append(self._current_form)
        if (
            self._current_form is not None
            and lowered in {"input", "textarea", "select", "button"}
        ):
            fields = self._current_form.get("fields")
            if isinstance(fields, list) and len(fields) < MAX_HTTP_FIELDS_PER_FORM:
                fields.append(
                    {
                        "tag": lowered,
                        "type": values.get("type", ""),
                        "name": values.get("name", ""),
                        "id": values.get("id", ""),
                        "placeholder": values.get("placeholder", ""),
                        "required": "required" in values,
                    }
                )

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript", "template"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if lowered == "title":
            self._in_title = False
        if lowered == "form":
            self._current_form = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data[:1000])
        self._append_text(data)


async def fetch_http_page(
    url: str,
    *,
    policy: BrowserDomainPolicy,
    max_bytes: int,
    timeout_seconds: int,
) -> HttpPageObservation:
    current = url
    source_url: str | None = None
    max_bytes = max(16_384, min(max_bytes, 2_000_000))
    timeout_seconds = max(2, min(timeout_seconds, 30))

    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout_seconds),
        headers={
            "User-Agent": "Aduoryn/1.0 governed-http-reader",
            "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
        },
    ) as client:
        for _ in range(MAX_HTTP_REDIRECTS + 1):
            decision = policy.permits(current, source_url=source_url)
            if not decision.allowed:
                raise BrowserNavigationBlocked(decision)
            egress = await evaluate_browser_egress(
                decision.target_url,
                allow_private_network=policy.allow_private_network,
            )
            if not egress.allowed:
                raise PermissionError(egress.reason)

            async with client.stream("GET", decision.target_url) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("location")
                    if not location:
                        break
                    source_url = decision.target_url
                    current = urljoin(source_url, location)
                    continue

                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type and not (
                    content_type.startswith("text/")
                    or content_type in {"application/xhtml+xml"}
                ):
                    raise ValueError(
                        f"HTTP-first reader only accepts text content, got {content_type}."
                    )

                body = bytearray()
                truncated = False
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    remaining = max_bytes - len(body)
                    if remaining <= 0:
                        truncated = True
                        break
                    body.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated = True
                        break

                encoding = response.encoding or "utf-8"
                text = bytes(body).decode(encoding, errors="replace")
                parser = _BoundedHTMLParser()
                if "html" in content_type or "<html" in text[:1000].lower():
                    parser.feed(text)
                    visible_text = parser.visible_text
                    title = parser.title
                    links = tuple(
                        {
                            "href": redact_url(urljoin(decision.target_url, item["href"])),
                            "text": redact_text(item["text"]),
                        }
                        for item in parser.links
                    )
                    forms = tuple(
                        {
                            **form,
                            "action": redact_url(
                                urljoin(decision.target_url, str(form.get("action") or ""))
                            ),
                        }
                        for form in parser.forms
                    )
                else:
                    visible_text = " ".join(text.split())[:MAX_HTTP_TEXT]
                    title = ""
                    links = ()
                    forms = ()

                return HttpPageObservation(
                    url=redact_url(str(response.url)),
                    status_code=response.status_code,
                    content_type=content_type or "text/plain",
                    title=redact_text(title),
                    visible_text=redact_text(visible_text),
                    links=links,
                    forms=forms,
                    truncated=truncated,
                )

    raise RuntimeError("HTTP-first reader exceeded the governed redirect limit.")
