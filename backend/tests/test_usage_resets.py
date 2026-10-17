"""Usage resets from the Console (commercial plan, phase 6): now or banked, for everyone, chosen
workspaces or plans; the plan and its period never change."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update

from app.api import billing_routes
from app.application.services import entitlements as plans
from app.application.services import usage_resets
from app.infrastructure.database import models as m
from tests.test_entitlements import give_plan
from tests.test_plan_prompts import db, fresh_cache, person  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def reset_table(db: Any) -> None:  # noqa: F811
    m.Base.metadata.create_all(db.engine, tables=[m.UsageReset.__table__])  # type: ignore[list-item]


def _grant(mode: str, target: dict[str, Any], **extra: Any) -> usage_resets.GrantDocument:
    return usage_resets.GrantDocument.model_validate({"grantId": str(uuid4()), "mode": mode, "target": target, **extra})


async def _used(db: Any, organization: Any) -> tuple[int, int]:  # noqa: F811
    async with db.session() as session:
        summary = await plans.summary(session, organization)
    assert summary is not None
    limits = {item["key"]: item["used"] for item in summary["limits"]}
    return limits["tasks.monthly"], limits["ai.tokens.monthly"]


@pytest.mark.asyncio
async def test_a_reset_now_clears_this_periods_usage_once_and_keeps_the_plan(db: Any) -> None:  # noqa: F811
    team, other = uuid4(), uuid4()
    for organization in (team, other):
        await give_plan(db, organization)
        await plans.count_usage(None, organization, tasks=3, ai_tokens=900)
    document = _grant("now", {"organizationIds": [str(team)]}, note="Outage credit")
    async with db.session() as session:
        before = await plans.current(session, team)
        assert await usage_resets.grant(session, document) == {"matched": 1, "granted": 1, "applied": 1}
        await session.commit()
        assert await usage_resets.grant(session, document) == {"matched": 1, "granted": 0, "applied": 0}
        reset = await session.scalar(select(m.UsageReset))
        event = await session.scalar(select(m.AuditEvent).where(m.AuditEvent.event_type == usage_resets.APPLIED))
        after = await plans.current(session, team)
    assert await _used(db, team) == (0, 0) and await _used(db, other) == (3, 900)
    assert reset is not None and reset.cleared and reset.cleared["tasks"] == 3 and reset.cleared["aiTokens"] == 900
    assert event is not None and event.payload["metadata"]["cleared"]["tasks"] == 3
    assert before is not None and after is not None and (after.version, after.period_start) == (before.version, before.period_start)
    await plans.count_usage(None, team, tasks=1)
    assert await _used(db, team) == (1, 0)


@pytest.mark.asyncio
async def test_a_banked_reset_waits_for_an_administrator_and_expires(db: Any) -> None:  # noqa: F811
    team, starter = uuid4(), uuid4()
    for organization in (team, starter):
        await give_plan(db, organization)
        await plans.count_usage(None, organization, tasks=2)
    async with db.session() as session:
        await session.execute(update(m.OrganizationEntitlement).where(
            m.OrganizationEntitlement.organization_id == starter).values(plan_code="starter"))
        result = await usage_resets.grant(session, _grant(
            "banked", {"planCodes": ["team"]}, expiresAt=(datetime.now(UTC) + timedelta(days=7)).isoformat()))
        await session.commit()
    assert result == {"matched": 1, "granted": 1, "applied": 0}
    assert await _used(db, team) == (2, 0)
    admin, member = await person(db, team, admin=True), await person(db, team, admin=False)
    async with db.session() as session:
        assert (await usage_resets.banked(session, team))["available"] == 1
        assert (await usage_resets.banked(session, starter))["available"] == 0
        with pytest.raises(HTTPException):
            await billing_routes.use_reset_v1(team, member, session)
        used = await billing_routes.use_reset_v1(team, admin, session)
    assert used["cleared"]["tasks"] == 2 and used["resets"]["available"] == 0
    assert await _used(db, team) == (0, 0)
    async with db.session() as session:
        with pytest.raises(HTTPException) as refused:
            await billing_routes.use_reset_v1(team, admin, session)
        assert refused.value.status_code == 409
        await session.execute(update(m.UsageReset).values(used_at=None, expires_at=datetime.now(UTC) - timedelta(days=1)))
        assert (await usage_resets.banked(session, team))["available"] == 0


@pytest.mark.asyncio
async def test_everyone_with_a_plan_and_grants_are_checked(db: Any) -> None:  # noqa: F811
    first, second = uuid4(), uuid4()
    for organization in (first, second):
        await give_plan(db, organization)
    async with db.session() as session:
        assert (await usage_resets.grant(session, _grant("banked", {"everyone": True})))["granted"] == 2
        with pytest.raises(ValueError, match="expiry"):
            await usage_resets.grant(session, _grant(
                "banked", {"everyone": True}, expiresAt=(datetime.now(UTC) - timedelta(hours=1)).isoformat()))
    for target in ({}, {"everyone": True, "planCodes": ["team"]}):
        with pytest.raises(ValidationError):
            _grant("now", target)
