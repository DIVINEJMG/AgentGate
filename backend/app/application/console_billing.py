"""Billing requests to the Console (commercial plan, phase 5): a workspace's plans and prices,
starting a checkout, its status, and the payment provider's management page; and the
announcements meant for the workspace app (`content.read`).

The Console owns prices, providers and subscriptions; AgentGate only asks, after checking the
person's permission in the workspace. Signed like the entitlement channel but with its own label
(``audoryn-billing-v1``), key and scopes. No redirects, no proxy settings from the environment,
bounded answers, and every answer is checked before use. Off unless configured; an invalid
configuration leaves it off and is logged by name only.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import UUID

import httpx

from audoryn_shared.application.console_entitlements import sign
from audoryn_shared.application.console_reads.config import decode_secret
from audoryn_shared.application.console_reads.signing import ServiceKey

logger = logging.getLogger("audoryn.console_billing")

SIGNATURE_VERSION = "audoryn-billing-v1"
PATH_PREFIX = "/api/v1/internal/agentgate/billing/v1"
READ_SCOPE, CHECKOUT_SCOPE, CONTENT_SCOPE = "billing.read", "billing.checkout", "content.read"
MAX_RESPONSE_BYTES = 256 * 1024
# What the Console's refusals mean for the person, as HTTP statuses.
_STATUS = {"invalid": 400, "not_found": 404, "not_offered": 409, "manage_existing": 409, "rate_limited": 429}


class BillingUnavailable(Exception):
    """The Console refused or could not answer; `message` is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = _STATUS.get(code, 503)


@dataclass(frozen=True)
class BillingChannel:
    origin: str
    key: ServiceKey = field(repr=False)
    timeout_seconds: float


def load_channel(settings: Any) -> BillingChannel | None:
    if not settings.console_billing_enabled:
        return None
    try:
        origin = urlparse(settings.console_billing_origin or "")
        local = origin.hostname in {"localhost", "127.0.0.1"}
        if not origin.hostname or origin.path not in {"", "/"} or origin.query or origin.username \
                or not (origin.scheme == "https" or (origin.scheme == "http" and local)):
            raise ValueError("CONSOLE_BILLING_ORIGIN must be a bare HTTPS origin (HTTP only for localhost)")
        secret = settings.console_billing_key_secret.get_secret_value().strip() \
            if settings.console_billing_key_secret is not None else ""
        if not settings.console_billing_key_id or not secret:
            raise ValueError("CONSOLE_BILLING_KEY_ID and _SECRET are required")
        key = ServiceKey(key_id=settings.console_billing_key_id, secret=decode_secret(secret),
                         audience=str(settings.console_billing_audience),
                         scopes=frozenset({READ_SCOPE, CHECKOUT_SCOPE, CONTENT_SCOPE}))
    except ValueError as exc:
        logger.error("console_billing.disabled_invalid_configuration problem=%s", exc)
        return None
    return BillingChannel(origin=f"{origin.scheme}://{origin.netloc}", key=key,
                          timeout_seconds=float(settings.console_billing_timeout_seconds))


class ConsoleBilling:
    def __init__(self, channel: BillingChannel, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._channel = channel
        self._transport = transport

    async def catalog(self, organization_id: UUID) -> dict[str, Any]:
        return await self._call("GET", "/catalog", READ_SCOPE, params={"organizationId": str(organization_id)})

    async def start_checkout(self, *, organization_id: UUID, user_id: UUID, email: str, plan_code: str,
                             billing_cycle: str, currency: str, request_key: str) -> dict[str, Any]:
        return await self._call("POST", "/checkouts", CHECKOUT_SCOPE, body={
            "organizationId": str(organization_id), "userId": str(user_id), "email": email, "planCode": plan_code,
            "billingCycle": billing_cycle, "currency": currency, "requestKey": request_key})

    async def checkout(self, organization_id: UUID, checkout_id: UUID) -> dict[str, Any]:
        return await self._call("GET", f"/checkouts/{checkout_id}", READ_SCOPE,
                                params={"organizationId": str(organization_id)})

    async def manage(self, organization_id: UUID) -> dict[str, Any]:
        return await self._call("POST", "/manage", CHECKOUT_SCOPE, body={"organizationId": str(organization_id)})

    async def content(self) -> dict[str, Any]:
        return await self._call("GET", "/content", CONTENT_SCOPE)

    async def _call(self, method: str, path: str, scope: str, *, params: dict[str, str] | None = None,
                    body: dict[str, object] | None = None) -> dict[str, Any]:
        target = PATH_PREFIX + path + (f"?{urlencode(params)}" if params else "")
        raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        key = self._channel.key
        headers = {"X-Audoryn-Service-Key": key.key_id, "X-Audoryn-Service-Audience": key.audience,
                   "X-Audoryn-Service-Scope": scope, "X-Audoryn-Service-Timestamp": timestamp,
                   "X-Audoryn-Service-Nonce": nonce, "X-Audoryn-Service-Signature": sign(
                       key, scope=scope, method=method, target=target, timestamp=timestamp, nonce=nonce, body=raw,
                       label=SIGNATURE_VERSION)}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self._channel.timeout_seconds, follow_redirects=False,
                                         trust_env=False, transport=self._transport) as client, \
                    client.stream(method, self._channel.origin + target, headers=headers, content=raw) as response:
                answer = b""
                async for chunk in response.aiter_bytes():
                    answer += chunk
                    if len(answer) > MAX_RESPONSE_BYTES:
                        raise BillingUnavailable("unavailable", "Billing is unavailable right now.")
        except httpx.HTTPError as exc:
            logger.warning("console_billing.unreachable path=%s category=%s", path.split("/")[1], type(exc).__name__)
            raise BillingUnavailable("unavailable", "Billing is unavailable right now. Try again shortly.") from exc
        try:
            payload = json.loads(answer)
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise BillingUnavailable("unavailable", "Billing is unavailable right now. Try again shortly.")
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", "unavailable"))
            if code in {"unauthorized", "unavailable"}:
                logger.warning("console_billing.refused code=%s", code)
            raise BillingUnavailable(code, str(error.get("message") or "Billing is unavailable right now."))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BillingUnavailable("unavailable", "Billing is unavailable right now. Try again shortly.")
        return data
