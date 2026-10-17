"""Billing requests to the Console (commercial plan, phase 5): the shared signature, the
configuration, the client's handling of answers, and who may buy."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api import billing_routes
from app.application import console_billing
from app.application.console_entitlements import sign
from app.application.console_reads.signing import ServiceKey
from app.domain.identity.principals import HumanPrincipal

VECTOR = json.loads((Path(__file__).resolve().parents[2] / "docs/integration/console-billing/signing-vector.json")
                    .read_text(encoding="utf-8"))
ORG = UUID("650c94c4-b642-4767-b9a9-6b56a0f53e83")
SECRET = base64.urlsafe_b64encode(b"k" * 32).decode()


def _settings(**values: Any) -> SimpleNamespace:
    base = {"console_billing_enabled": True, "console_billing_origin": "https://console.audoryn.test",
            "console_billing_audience": "console", "console_billing_key_id": "agentgate-billing",
            "console_billing_key_secret": SimpleNamespace(get_secret_value=lambda: SECRET),
            "console_billing_timeout_seconds": 5}
    return SimpleNamespace(**{**base, **values})


def test_the_shared_vector_signs_the_same_as_the_console() -> None:
    secret = base64.urlsafe_b64decode(VECTOR["secretBase64url"] + "=" * (-len(VECTOR["secretBase64url"]) % 4))
    key = ServiceKey(VECTOR["keyId"], secret, VECTOR["audience"], frozenset({VECTOR["scope"]}))
    assert sign(key, scope=VECTOR["scope"], method=VECTOR["method"], target=VECTOR["target"],
                timestamp=VECTOR["timestamp"], nonce=VECTOR["nonce"], body=VECTOR["body"].encode(),
                label=console_billing.SIGNATURE_VERSION) == VECTOR["signature"]


def test_the_channel_is_off_unless_fully_and_safely_configured() -> None:
    assert console_billing.load_channel(_settings(console_billing_enabled=False)) is None
    for bad in ({"console_billing_origin": "http://console.audoryn.test"}, {"console_billing_origin": "https://c/x"},
                {"console_billing_key_id": None}):
        assert console_billing.load_channel(_settings(**bad)) is None
    assert console_billing.load_channel(_settings(console_billing_origin="http://localhost:8001")) is not None


@pytest.mark.asyncio
async def test_requests_are_signed_and_answers_checked() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/catalog"):
            return httpx.Response(200, json={"version": 1, "data": {"plans": []}})
        if request.url.path.endswith("/checkouts"):
            return httpx.Response(409, json={"version": 1, "error": {"code": "not_offered", "message": "Not offered."}})
        return httpx.Response(302, headers={"location": "https://elsewhere.example"})

    channel = console_billing.load_channel(_settings())
    assert channel is not None
    client = console_billing.ConsoleBilling(channel, transport=httpx.MockTransport(answer))
    assert await client.catalog(ORG) == {"plans": []}
    request = seen[0]
    target = request.url.raw_path.decode()
    assert target == f"{console_billing.PATH_PREFIX}/catalog?organizationId={ORG}"
    assert request.headers["x-audoryn-service-signature"] == sign(
        channel.key, scope="billing.read", method="GET", target=target,
        timestamp=request.headers["x-audoryn-service-timestamp"], nonce=request.headers["x-audoryn-service-nonce"],
        body=b"", label="audoryn-billing-v1")
    with pytest.raises(console_billing.BillingUnavailable) as refused:
        await client.start_checkout(organization_id=ORG, user_id=uuid4(), email="a@b.co", plan_code="team",
                                    billing_cycle="monthly", currency="USD", request_key="request-0001")
    assert (refused.value.code, refused.value.status) == ("not_offered", 409)
    with pytest.raises(console_billing.BillingUnavailable) as moved:  # a redirect is never followed
        await client.manage(ORG)
    assert moved.value.status == 503 and len(seen) == 3


def _principal(*permissions: str) -> HumanPrincipal:
    return HumanPrincipal(user_id=uuid4(), organization_id=ORG, membership_id=uuid4(), role="admin",
                          permissions=frozenset(permissions))


class FakeBilling:
    def __init__(self, url: str) -> None:
        self.url = url
        self.started: dict[str, Any] = {}

    async def catalog(self, organization_id: UUID) -> dict[str, Any]:
        return {"providers": [{"name": "paystack", "checkoutHosts": ["checkout.paystack.com"]}]}

    async def start_checkout(self, **values: Any) -> dict[str, Any]:
        self.started = values
        return {"id": str(uuid4()), "status": "open", "provider": "paystack", "url": self.url}


def _checkout() -> billing_routes.CheckoutRequest:
    return billing_routes.CheckoutRequest.model_validate(
        {"planCode": "team", "billingCycle": "monthly", "currency": "NGN", "requestKey": "request-0001"})


@pytest.mark.asyncio
async def test_only_managers_buy_with_their_own_email_and_only_to_the_providers_page(
        monkeypatch: pytest.MonkeyPatch) -> None:
    session = cast(Any, SimpleNamespace(scalar=AsyncMock(return_value="owner@audoryn.test"), commit=AsyncMock()))
    audit = AsyncMock()
    monkeypatch.setattr(billing_routes, "append_audit", audit)
    with pytest.raises(HTTPException) as forbidden:
        await billing_routes.checkout_v1(ORG, _checkout(), _principal("organizations.read"), session)
    assert forbidden.value.status_code == 403
    good = FakeBilling("https://checkout.paystack.com/abc")
    monkeypatch.setattr(billing_routes, "_billing", lambda: good)
    principal = _principal("organizations.read", "organizations.manage")
    result = await billing_routes.checkout_v1(ORG, _checkout(), principal, session)
    assert result["url"] == "https://checkout.paystack.com/abc"
    assert good.started["email"] == "owner@audoryn.test" and good.started["user_id"] == principal.user_id
    assert audit.await_args is not None and audit.await_args.kwargs["event_type"] == "commercial.checkout.started"
    monkeypatch.setattr(billing_routes, "_billing", lambda: FakeBilling("https://evil.example/pay"))
    with pytest.raises(HTTPException) as unexpected:
        await billing_routes.checkout_v1(ORG, _checkout(), principal, session)
    assert unexpected.value.status_code == 502


def test_the_page_can_only_ask_for_a_plan_cycle_and_currency() -> None:
    with pytest.raises(ValueError):
        billing_routes.CheckoutRequest.model_validate({"planCode": "team", "billingCycle": "monthly", "currency": "NGN",
                                                       "requestKey": "request-0001", "amountMinor": 1})
    with pytest.raises(ValueError):
        billing_routes.CheckoutRequest.model_validate({"planCode": "team", "billingCycle": "weekly", "currency": "NGN",
                                                       "requestKey": "request-0001"})
