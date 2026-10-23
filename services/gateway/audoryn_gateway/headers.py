"""What the gateway forwards: hop-by-hop headers stop here, everything else passes unchanged.

The original `Host` is kept, so the core sees the URL the caller used. QStash signs that URL and
the core checks the signature against it, and OAuth callbacks are built from it."""
from __future__ import annotations

import re
from collections.abc import Iterable
from uuid import uuid4

CORRELATION = "x-correlation-id"
_HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
                         "trailer", "trailers", "transfer-encoding", "upgrade"})
# The gateway is the edge: these describe the caller, so they are set here, never taken from the caller.
_SET_HERE = frozenset({"x-forwarded-for", "x-forwarded-proto", CORRELATION})
_VALID_CORRELATION = re.compile(r"[A-Za-z0-9._:-]{8,128}")

Headers = list[tuple[bytes, bytes]]


def correlation_id(headers: Iterable[tuple[bytes, bytes]]) -> str:
    """The caller's correlation id when it is well formed, otherwise a new one."""
    given = next((value.decode("latin-1") for name, value in headers if name.lower() == CORRELATION.encode()), "")
    return given if _VALID_CORRELATION.fullmatch(given) else str(uuid4())


def _dropped(headers: Iterable[tuple[bytes, bytes]]) -> frozenset[str]:
    named = {token.strip().lower() for name, value in headers if name.lower() == b"connection"
             for token in value.decode("latin-1").split(",")}
    return _HOP_BY_HOP | named


def upstream_request(headers: Headers, *, client: str | None, scheme: str, correlation: str) -> Headers:
    drop = _dropped(headers) | _SET_HERE
    forwarded = [(name, value) for name, value in headers if name.decode("latin-1").lower() not in drop]
    forwarded += [(b"x-forwarded-proto", scheme.encode()), (CORRELATION.encode(), correlation.encode())]
    if client:
        forwarded.append((b"x-forwarded-for", client.encode()))
    return forwarded


def downstream_response(headers: Headers, *, correlation: str) -> Headers:
    drop = _dropped(headers) | {CORRELATION}
    kept = [(name, value) for name, value in headers if name.decode("latin-1").lower() not in drop]
    return [*kept, (CORRELATION.encode(), correlation.encode())]
