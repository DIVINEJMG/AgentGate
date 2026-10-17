"""Upgrade prompts (plans, news and upgrade prompts plan, part 2): which banner a person sees and
how often, the plan's features, members asking administrators to upgrade, and the limit named on
a refusal."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import billing_routes
from app.application.services import entitlements as plans
from app.application.services import plan_prompts
from app.bootstrap.settings import settings
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database import models as m
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes
from tests.test_entitlements import TABLES, give_plan

PROMPT_TABLES = (*TABLES, m.HumanIdentity, m.UserContentState, m.AuditEvent)


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    plans.forget()
    yield
    plans.forget()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    m.Base.metadata.create_all(engine, tables=[cast(Any, table.__table__) for table in PROMPT_TABLES])
    from app.infrastructure.database import session as session_module

    monkeypatch.setattr(session_module, "session_factory", lambda: BenchSession(Session(engine)))
    monkeypatch.setattr(settings, "entitlements_enforcement", True)
    with _aware_sqlite_datetimes():
        yield SimpleNamespace(engine=engine, session=lambda: BenchSession(Session(engine)))


async def person(db: Any, organization: UUID, *, admin: bool, name: str = "Ada") -> HumanPrincipal:
    user = uuid4()
    async with db.session() as session:
        session.add(m.HumanIdentity(id=user, subject=f"local:{user}", display_name=name))
        await session.commit()
    permissions = {"organizations.read", "organizations.manage"} if admin else {"organizations.read"}
    return HumanPrincipal(user_id=user, organization_id=organization, membership_id=uuid4(),
                          role="admin" if admin else "operator", permissions=frozenset(permissions))


async def prompts(db: Any, organization: UUID, principal: HumanPrincipal) -> dict[str, Any]:
    async with db.session() as session:
        return await plan_prompts.status(session, organization, principal)


@pytest.mark.asyncio
async def test_the_banner_names_the_fullest_limit_and_stays_away_a_week_after_dismissal(db: Any) -> None:
    organization = uuid4()
    await give_plan(db, organization)
    await plans.count_usage(None, organization, tasks=3, ai_tokens=850)
    member = await person(db, organization, admin=False)
    shown = await prompts(db, organization, member)
    assert [limit["key"] for limit in shown["limits"]] == ["tasks.monthly", "ai.tokens.monthly"]
    assert shown["banner"] == "tasks.monthly" and shown["canUpgrade"] is False and shown["requests"] == []
    assert shown["features"] == {"browser.enabled": True, "coding.enabled": False, "events.enabled": False}

    async with db.session() as session:
        await plan_prompts.dismiss(session, organization, member.user_id, "tasks.monthly")
        await session.commit()
    assert (await prompts(db, organization, member))["banner"] == "ai.tokens.monthly"
    # Someone else in the workspace still sees it; the dismissal is this person's.
    assert (await prompts(db, organization, await person(db, organization, admin=False)))["banner"] == "tasks.monthly"

    async with db.session() as session:
        await session.execute(update(m.UserContentState).values(dismissed_at=datetime.now(UTC) - timedelta(days=8)))
        await session.commit()
    assert (await prompts(db, organization, member))["banner"] == "tasks.monthly"


@pytest.mark.asyncio
async def test_nothing_is_suggested_while_the_plan_is_not_enforced(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    organization = uuid4()
    await give_plan(db, organization)
    await plans.count_usage(None, organization, tasks=3)
    monkeypatch.setattr(settings, "entitlements_enforcement", False)
    shown = await prompts(db, organization, await person(db, organization, admin=True))
    assert (shown["banner"], shown["limits"], shown["features"]) == (None, [], {})
    assert shown["plan"]["code"] == "team" and shown["plan"]["enforced"] is False


@pytest.mark.asyncio
async def test_members_ask_administrators_once_a_day_per_limit(db: Any) -> None:
    organization = uuid4()
    await give_plan(db, organization)
    member = await person(db, organization, admin=False, name="Grace")
    admin = await person(db, organization, admin=True)

    async def ask(key: str) -> dict[str, Any]:
        async with db.session() as session:
            return await billing_routes.upgrade_request_v1(organization, billing_routes.PromptKey(key=key),
                                                           member, cast(Any, session))

    assert (await ask("tasks.monthly"))["sent"] is True
    assert (await ask("tasks.monthly"))["sent"] is False
    assert (await ask("coding.enabled"))["sent"] is True
    requests = (await prompts(db, organization, admin))["requests"]
    assert [(request["limit"], request["requestedBy"]) for request in requests] == [
        ("coding.enabled", "Grace"), ("tasks.monthly", "Grace")]
    assert requests[1]["name"] == "tasks"

    async with db.session() as session:
        await plan_prompts.dismiss(session, organization, admin.user_id, f"request:{requests[0]['id']}")
        await session.execute(update(m.AuditEvent).values(created_at=datetime.now(UTC) - timedelta(days=2)))
        await session.commit()
    assert [request["limit"] for request in (await prompts(db, organization, admin))["requests"]] == ["tasks.monthly"]
    assert (await ask("tasks.monthly"))["sent"] is True  # a day later, the administrators hear again


def test_only_known_prompts_are_accepted() -> None:
    assert plan_prompts.valid_key("tasks.monthly") and plan_prompts.valid_key("plan.status")
    assert plan_prompts.valid_key(f"request:{uuid4()}")
    for bad in ("request:nope", "anything", "request:", "prompt:x"):
        assert not plan_prompts.valid_key(bad)
    with pytest.raises(HTTPException) as refused:
        billing_routes._prompt_key(billing_routes.PromptKey(key="nope"))
    assert refused.value.status_code == 422


@pytest.mark.asyncio
async def test_a_refusal_names_the_limit_for_the_app(db: Any) -> None:
    organization = uuid4()
    await give_plan(db, organization)
    await plans.count_usage(None, organization, tasks=3)
    with pytest.raises(plans.EntitlementDenied) as refused:
        await plans.require_allowance(None, organization, *plans.TASK_ALLOWANCES)
    assert refused.value.status_code == 402 and refused.value.headers == {"X-Plan-Limit": "tasks.monthly"}
    with pytest.raises(plans.EntitlementDenied) as feature:
        await plans.require_feature(None, organization, "coding.enabled")
    assert feature.value.limit == "coding.enabled"
