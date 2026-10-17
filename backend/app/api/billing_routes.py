"""A workspace's plan purchases (commercial plan, phase 5). AgentGate checks who may buy; the
Console owns prices, providers and subscriptions (`app/application/console_billing.py`).

Only people who manage the workspace may start a checkout or open billing management, and the
email sent to the provider is their own account's. The plan changes only when the provider's
confirmed payment reaches the Console, never because a browser came back from a checkout."""
from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import append_audit, require_permission
from app.application.console_billing import BillingUnavailable, ConsoleBilling, load_channel
from app.application.services import entitlements, plan_prompts
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.models import HumanIdentity
from app.infrastructure.database.session import database_session

v1_router = APIRouter(tags=["commercial"])
v2_router = APIRouter(tags=["commercial"])


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    plan_code: str = Field(alias="planCode", pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    billing_cycle: str = Field(alias="billingCycle", pattern=r"^(monthly|annual)$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # Chosen by the page once per click, so a repeated click opens the same checkout.
    request_key: str = Field(alias="requestKey", pattern=r"^[A-Za-z0-9_-]{8,64}$")


class PromptKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=80)


def _prompt_key(payload: PromptKey) -> str:
    if not plan_prompts.valid_key(payload.key):
        raise HTTPException(422, "Unknown prompt.")
    return payload.key


def _billing() -> ConsoleBilling:
    channel = load_channel(settings)
    if channel is None:
        raise HTTPException(404, "Online checkout is not available. Ask your administrator to change the plan.")
    return ConsoleBilling(channel)


async def _ask(call: Any) -> dict[str, Any]:
    try:
        return await call
    except BillingUnavailable as refused:
        raise HTTPException(refused.status, refused.message) from refused


def _safe_url(url: object, hosts: set[str]) -> str:
    """A page the browser may be sent to: HTTPS (HTTP only for localhost) on an expected host."""
    target = urlparse(str(url or ""))
    local = target.hostname in {"localhost", "127.0.0.1"}
    if not (target.scheme == "https" or (target.scheme == "http" and local)) or target.hostname not in hosts:
        raise HTTPException(502, "Billing returned an unexpected address.")
    return str(url)


@v1_router.get("/organizations/{organization_id}/billing/catalog")
async def catalog_v1(organization_id: UUID,
                     principal: Annotated[HumanPrincipal, Depends(organization_principal)]) -> dict[str, Any]:
    require_permission(principal, "organizations.read")
    return {**await _ask(_billing().catalog(organization_id)), "comparison": entitlements.comparison()}


@v1_router.post("/organizations/{organization_id}/billing/checkout")
async def checkout_v1(organization_id: UUID, payload: CheckoutRequest,
                      principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                      session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    require_permission(principal, "organizations.manage")
    email = await session.scalar(select(HumanIdentity.email).where(HumanIdentity.id == principal.user_id))
    if not email:
        raise HTTPException(409, "Your account needs an email address to pay for a plan.")
    billing = _billing()
    hosts = {host for provider in (await _ask(billing.catalog(organization_id))).get("providers", [])
             for host in provider.get("checkoutHosts", [])}
    checkout = await _ask(billing.start_checkout(
        organization_id=organization_id, user_id=principal.user_id, email=email, plan_code=payload.plan_code,
        billing_cycle=payload.billing_cycle, currency=payload.currency, request_key=payload.request_key))
    checkout["url"] = _safe_url(checkout.get("url"), hosts)
    await append_audit(session, organization_id=organization_id, principal=principal,
                       event_type="commercial.checkout.started", category="system", resource_type="checkout",
                       resource_id=str(checkout.get("id")), outcome="started",
                       summary=f"Checkout started for the {payload.plan_code} plan.",
                       metadata={"planCode": payload.plan_code, "billingCycle": payload.billing_cycle,
                                 "currency": payload.currency, "provider": checkout.get("provider")})
    await session.commit()
    return checkout


@v1_router.get("/organizations/{organization_id}/billing/checkouts/{checkout_id}")
async def checkout_status_v1(organization_id: UUID, checkout_id: UUID,
                             principal: Annotated[HumanPrincipal, Depends(organization_principal)]) -> dict[str, Any]:
    require_permission(principal, "organizations.read")
    checkout = await _ask(_billing().checkout(organization_id, checkout_id))
    return {key: checkout.get(key) for key in ("id", "status", "provider", "billingCycle", "currency", "failureReason")}


@v1_router.post("/organizations/{organization_id}/billing/manage")
async def manage_v1(organization_id: UUID,
                    principal: Annotated[HumanPrincipal, Depends(organization_principal)]) -> dict[str, Any]:
    require_permission(principal, "organizations.manage")
    answer = await _ask(_billing().manage(organization_id))
    # Paddle's customer portal and Paystack's management pages are on the providers' own hosts.
    host = urlparse(str(answer.get("url") or "")).hostname or ""
    if not (host == "paystack.com" or host.endswith((".paystack.com", ".paddle.com")) or host == "paddle.com"):
        raise HTTPException(502, "Billing returned an unexpected address.")
    return {"url": answer["url"]}


@v1_router.get("/organizations/{organization_id}/plan/prompts")
async def prompts_v1(organization_id: UUID, principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                     session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    require_permission(principal, "organizations.read")
    return await plan_prompts.status(session, organization_id, principal)


@v1_router.post("/organizations/{organization_id}/plan/prompts/dismiss")
async def dismiss_prompt_v1(organization_id: UUID, payload: PromptKey,
                            principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                            session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    require_permission(principal, "organizations.read")
    await plan_prompts.dismiss(session, organization_id, principal.user_id, _prompt_key(payload))
    await session.commit()
    return {"dismissed": True}


@v1_router.post("/organizations/{organization_id}/plan/upgrade-requests")
async def upgrade_request_v1(organization_id: UUID, payload: PromptKey,
                             principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                             session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    """A member asks the administrators for a bigger plan; once a day per workspace and limit."""
    require_permission(principal, "organizations.read")
    key = _prompt_key(payload)
    if key not in plan_prompts.PROMPT_KEYS:
        raise HTTPException(422, "Unknown limit.")
    earlier = await plan_prompts.last_request(session, organization_id, key)
    if earlier is not None:
        return {"sent": False, "requestedAt": earlier.isoformat()}
    person = await session.scalar(select(HumanIdentity.display_name).where(HumanIdentity.id == principal.user_id))
    name = entitlements.limit_name(key)
    event = await append_audit(session, organization_id=organization_id, principal=principal,
                               event_type=plan_prompts.REQUESTED, category="system", resource_type="plan",
                               resource_id=key, outcome="requested",
                               summary=f"{person or 'A member'} asked to upgrade the plan ({name}).",
                               metadata={"limit": key, "name": name, "requestedBy": person})
    await session.commit()
    return {"sent": True, "requestedAt": event.created_at.isoformat()}


@v2_router.get("/organizations/{organization_id}/billing/catalog")
async def catalog_v2(organization_id: UUID,
                     principal: Annotated[HumanPrincipal, Depends(organization_principal)]) -> dict[str, Any]:
    return {"data": await catalog_v1(organization_id, principal)}


@v2_router.post("/organizations/{organization_id}/billing/checkout")
async def checkout_v2(organization_id: UUID, payload: CheckoutRequest,
                      principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                      session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    return {"data": await checkout_v1(organization_id, payload, principal, session)}


@v2_router.get("/organizations/{organization_id}/billing/checkouts/{checkout_id}")
async def checkout_status_v2(organization_id: UUID, checkout_id: UUID,
                             principal: Annotated[HumanPrincipal, Depends(organization_principal)]) -> dict[str, Any]:
    return {"data": await checkout_status_v1(organization_id, checkout_id, principal)}


@v2_router.post("/organizations/{organization_id}/billing/manage")
async def manage_v2(organization_id: UUID,
                    principal: Annotated[HumanPrincipal, Depends(organization_principal)]) -> dict[str, Any]:
    return {"data": await manage_v1(organization_id, principal)}


@v2_router.get("/organizations/{organization_id}/plan/prompts")
async def prompts_v2(organization_id: UUID, principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                     session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    return {"data": await prompts_v1(organization_id, principal, session)}


@v2_router.post("/organizations/{organization_id}/plan/prompts/dismiss")
async def dismiss_prompt_v2(organization_id: UUID, payload: PromptKey,
                            principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                            session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    return {"data": await dismiss_prompt_v1(organization_id, payload, principal, session)}


@v2_router.post("/organizations/{organization_id}/plan/upgrade-requests")
async def upgrade_request_v2(organization_id: UUID, payload: PromptKey,
                             principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                             session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    return {"data": await upgrade_request_v1(organization_id, payload, principal, session)}
