"""What the gateway answers as the edge (Phase 1d): CORS and security headers.

CORS uses Starlette's own middleware with the core's rules, so a browser sees exactly what the core
answered before. Exactly one of the two may answer CORS: browsers refuse a reply carrying its
headers twice, so `GATEWAY_CORS_ENABLED=true` goes together with the core's `CORS_ENABLED=false`.
The backend's tests check that `CORS_RULES` here matches the core's.
"""
from __future__ import annotations

from typing import Any

from starlette.middleware.cors import CORSMiddleware

from audoryn_gateway.settings import GatewaySettings

CORS_RULES = {
    "allow_credentials": True,
    "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    "allow_headers": ["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-ID"],
    # Which plan limit refused new work (402), so the app can show its upgrade prompt.
    "expose_headers": ["X-Plan-Limit"],
}
# Added only when the reply doesn't set its own (the GitHub sign-in pages set Referrer-Policy).
_SECURITY = ((b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
             (b"referrer-policy", b"strict-origin-when-cross-origin"))


class SecurityHeaders:
    def __init__(self, app: Any, hsts_seconds: int) -> None:
        self.app = app
        self.headers = _SECURITY + (
            ((b"strict-transport-security", f"max-age={hsts_seconds}; includeSubDomains".encode()),)
            if hsts_seconds else ())

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = [*message.get("headers", []),
                                      *(header for header in self.headers if header[0] not in present)]
            await send(message)

        await self.app(scope, receive, with_headers)


def at_the_edge(app: Any, settings: GatewaySettings) -> Any:
    """The gateway wrapped in what the edge answers, each part only when switched on."""
    if settings.security_headers:
        app = SecurityHeaders(app, settings.hsts_seconds)
    if settings.cors_enabled:
        app = CORSMiddleware(app, allow_origins=settings.cors_allowed_origins, **CORS_RULES)
    return app
