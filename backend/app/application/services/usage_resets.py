"""Usage resets (commercial plan, phase 6): the Console gives a workspace its usage back without
changing its plan or period, like a provider granting an extra reset.

A grant reaches everyone with a plan, chosen workspaces, or the workspaces on chosen plans. `now`
clears this period's usage when it arrives; `banked` waits, until it expires, for someone who
manages the workspace to use it. Each reset keeps the usage it cleared and is audited; the normal
reset at the end of the period still happens. Workers, members and other standing counts are not
usage and are never touched.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.product_common import append_audit
from app.application.services import entitlements
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.models import (
    OrganizationEntitlement,
    OrganizationUsage,
    UsageReset,
)

APPLIED = "commercial.usage.reset"
_COUNTERS = {"tasks": "tasks", "aiTokens": "ai_tokens", "browserSeconds": "browser_seconds",
             "codingSeconds": "coding_seconds", "webResearch": "web_research"}


class Target(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    everyone: bool = False
    organization_ids: list[UUID] = Field(default_factory=list, alias="organizationIds", max_length=500)
    plan_codes: list[str] = Field(default_factory=list, alias="planCodes", max_length=20)

    @model_validator(mode="after")
    def _one(self) -> Target:
        if sum((self.everyone, bool(self.organization_ids), bool(self.plan_codes))) != 1:
            raise ValueError("Choose everyone, workspaces or plans.")
        return self


class GrantDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    grant_id: UUID = Field(alias="grantId")
    mode: Literal["now", "banked"]
    target: Target
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    note: str | None = Field(default=None, max_length=200)


async def _targets(session: AsyncSession, target: Target) -> list[UUID]:
    """Workspaces with a plan that the grant reaches; unknown ids are skipped."""
    query = select(OrganizationEntitlement.organization_id)
    if target.organization_ids:
        query = query.where(OrganizationEntitlement.organization_id.in_(target.organization_ids))
    elif target.plan_codes:
        query = query.where(OrganizationEntitlement.plan_code.in_(target.plan_codes))
    return list((await session.scalars(query)).all())


async def grant(session: AsyncSession, document: GrantDocument) -> dict[str, int]:
    """Records the grant once per workspace (a repeated grant id changes nothing) and applies a
    `now` grant at once."""
    if document.expires_at is not None and document.expires_at <= datetime.now(UTC):
        raise ValueError("The expiry is in the past.")
    organizations = await _targets(session, document.target)
    # A retried grant finds its workspaces already given; the unique key stops a concurrent repeat.
    given = set((await session.scalars(select(UsageReset.organization_id).where(
        UsageReset.grant_id == document.grant_id))).all())
    created = [UsageReset(grant_id=document.grant_id, organization_id=organization, mode=document.mode,
                          note=document.note, expires_at=document.expires_at if document.mode == "banked" else None)
               for organization in organizations if organization not in given]
    session.add_all(created)
    await session.flush()
    if document.mode == "now":
        for reset in created:
            await _apply(session, reset, None)
    return {"matched": len(organizations), "granted": len(created),
            "applied": len(created) if document.mode == "now" else 0}


def _available(organization_id: UUID, now: datetime) -> Any:
    return select(UsageReset).where(
        UsageReset.organization_id == organization_id, UsageReset.mode == "banked", UsageReset.used_at.is_(None),
        (UsageReset.expires_at.is_(None)) | (UsageReset.expires_at > now))


async def banked(session: AsyncSession, organization_id: UUID) -> dict[str, Any]:
    """Resets this workspace can use now, soonest to expire first."""
    rows = (await session.scalars(_available(organization_id, datetime.now(UTC)).order_by(
        UsageReset.expires_at.asc().nulls_last()))).all()
    return {"available": len(rows), "nextExpiresAt": rows[0].expires_at.isoformat() if rows and rows[0].expires_at else None}


async def use(session: AsyncSession, organization_id: UUID, principal: HumanPrincipal) -> dict[str, Any] | None:
    """Uses the banked reset that expires soonest; None when there is none."""
    reset = await session.scalar(_available(organization_id, datetime.now(UTC)).order_by(
        UsageReset.expires_at.asc().nulls_last()).limit(1).with_for_update(skip_locked=True))
    if reset is None:
        return None
    await _apply(session, reset, principal)
    return {"cleared": reset.cleared, "periodStart": reset.period_start.isoformat() if reset.period_start else None}


async def _apply(session: AsyncSession, reset: UsageReset, principal: HumanPrincipal | None) -> None:
    """Sets this period's usage back to zero and keeps what it was."""
    now = datetime.now(UTC)
    plan = await entitlements.current(session, reset.organization_id)
    start = plan.period_at(now)[0] if plan is not None else None
    usage = await session.get(OrganizationUsage, (reset.organization_id, start), with_for_update=True) if start else None
    cleared = {name: getattr(usage, column) for name, column in _COUNTERS.items()} if usage else {}
    if usage is not None:
        for column in _COUNTERS.values():
            setattr(usage, column, 0)
        usage.updated_at = now
    reset.used_at, reset.used_by, reset.period_start, reset.cleared = (
        now, principal.user_id if principal else None, start, cleared)
    await append_audit(session, organization_id=reset.organization_id, principal=principal, event_type=APPLIED,
                       category="system", resource_type="plan", resource_id=str(reset.id), outcome="reset",
                       summary="This period's usage was reset" + (" by the Console." if principal is None else "."),
                       metadata={"grantId": str(reset.grant_id), "mode": reset.mode, "note": reset.note,
                                 "cleared": cleared, "periodStart": start.isoformat() if start else None})
