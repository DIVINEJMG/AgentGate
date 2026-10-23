"""First-run setup of a new workspace: welcome answers, starter setup and the checklist."""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.onboarding import onboarding_public, set_up_workspace
from audoryn_shared.api.auth_dependencies import organization_principal
from audoryn_shared.api.product_common import require_permission
from audoryn_shared.domain.identity.principals import HumanPrincipal
from audoryn_shared.infrastructure.database.models import HumanIdentity, OrganizationOnboarding
from audoryn_shared.infrastructure.database.session import database_session

router = APIRouter(tags=["onboarding"])
Principal = Annotated[HumanPrincipal, Depends(organization_principal)]
Session = Annotated[AsyncSession, Depends(database_session)]


class SetupInput(BaseModel):
    goal: str | None = Field(default=None, max_length=32)
    tools: list[str] = Field(default_factory=list, max_length=12)
    care: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)


class ChecklistInput(BaseModel):
    dismissed: bool


@router.get("/organizations/{organization_id}/onboarding")
async def onboarding(organization_id: UUID, principal: Principal, session: Session) -> dict[str, Any]:
    return await onboarding_public(session, organization_id)


@router.post("/organizations/{organization_id}/onboarding/setup")
async def setup(organization_id: UUID, body: SetupInput, principal: Principal, session: Session) -> dict[str, Any]:
    require_permission(principal, "workforce.manage")
    user = await session.get(HumanIdentity, principal.user_id)
    person = ((user.display_name or "").split() or [""])[0] if user else ""
    try:
        await set_up_workspace(session, organization_id, principal, body.model_dump(), person=person)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return await onboarding_public(session, organization_id)


@router.post("/organizations/{organization_id}/onboarding/checklist")
async def checklist(organization_id: UUID, body: ChecklistInput, principal: Principal, session: Session) -> dict[str, Any]:
    row = await session.scalar(select(OrganizationOnboarding).where(
        OrganizationOnboarding.organization_id == organization_id))
    if row is None:
        raise HTTPException(404, "This workspace has no getting-started checklist.")
    row.checklist_dismissed = body.dismissed
    await session.commit()
    return {"checklistDismissed": row.checklist_dismissed}
