"""The workspace app's "What's new": announcements for this person and their read state
(`services/news.py`). The CMS changelog is served by the website server (`/_public/changelog`)."""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import require_permission
from app.application.console_billing import ConsoleBilling, load_channel
from app.application.services import content_state, news
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.models import OrganizationEntitlement
from app.infrastructure.database.session import database_session

v1_router = APIRouter(tags=["commercial"])
v2_router = APIRouter(tags=["commercial"])


class NewsState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[str] = Field(min_length=1, max_length=50)
    mark: Literal["read", "dismissed"]


def _time(value: Any) -> str | None:
    return value.isoformat() if value else None


@v1_router.get("/organizations/{organization_id}/news")
async def news_v1(organization_id: UUID, principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                  session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    require_permission(principal, "organizations.read")
    channel = load_channel(settings)
    plan_code = await session.scalar(select(OrganizationEntitlement.plan_code).where(
        OrganizationEntitlement.organization_id == organization_id))
    items = news.for_person(await news.announcements(ConsoleBilling(channel) if channel else None),
                            plan_code=plan_code, role=principal.role)
    state = await content_state.states(session, principal.user_id, prefix=news.PREFIX)
    return {
        "announcements": items,
        "state": {key: {"readAt": _time(read), "dismissedAt": _time(dismissed)} for key, (read, dismissed) in state.items()},
        "canUpgrade": "organizations.manage" in principal.permissions,
    }


@v1_router.post("/organizations/{organization_id}/news/state")
async def news_state_v1(organization_id: UUID, payload: NewsState,
                        principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                        session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    require_permission(principal, "organizations.read")
    if not all(news.KEY.fullmatch(key) for key in payload.keys):
        raise HTTPException(422, "Unknown news entry.")
    await content_state.mark(session, principal.user_id, payload.keys, payload.mark)
    await session.commit()
    return {"marked": len(set(payload.keys))}


@v2_router.get("/organizations/{organization_id}/news")
async def news_v2(organization_id: UUID, principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                  session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    return {"data": await news_v1(organization_id, principal, session)}


@v2_router.post("/organizations/{organization_id}/news/state")
async def news_state_v2(organization_id: UUID, payload: NewsState,
                        principal: Annotated[HumanPrincipal, Depends(organization_principal)],
                        session: Annotated[AsyncSession, Depends(database_session)]) -> dict[str, Any]:
    return {"data": await news_state_v1(organization_id, payload, principal, session)}
