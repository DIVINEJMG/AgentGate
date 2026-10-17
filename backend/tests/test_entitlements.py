"""Plan limits from the Console (commercial plan, phase 2): one service answers every check, only
new work is refused, and nothing is read or refused while enforcement is off."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.application.services import entitlements as plans
from app.bootstrap.settings import settings
from app.infrastructure.database import models as m
from app.infrastructure.database import usage_records
from app.observability.usage import Usage, count_plan_usage, metered
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes

TABLES = (m.Organization, m.OrganizationEntitlement, m.EntitlementSnapshot, m.EntitlementTemplate, m.OrganizationUsage, m.Worker,
          m.Job, m.JobRevision, m.OrganizationMembership, m.Integration, m.Artifact, m.UsageRecord)
LIMITS: dict[str, bool | int] = {
    "workers.max": 2, "jobs.per_worker.max": 1, "members.max": 2, "connections.max": 1, "tasks.monthly": 3,
    "ai.tokens.monthly": 1000, "coding.seconds.monthly": 60, "web_research.monthly": 2, "storage.bytes": 100,
    "browser.enabled": True, "coding.enabled": False, "browser.sessions.concurrent": 2,
    "schedules.min_interval_minutes": 15,
}


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    plans.forget()
    yield
    plans.forget()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    m.Base.metadata.create_all(engine, tables=[cast(Any, table.__table__) for table in TABLES])
    from app.infrastructure.database import session as session_module

    monkeypatch.setattr(session_module, "session_factory", lambda: BenchSession(Session(engine)))
    with _aware_sqlite_datetimes():
        yield SimpleNamespace(engine=engine, session=lambda: BenchSession(Session(engine)))


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "entitlements_enforcement", True)


def snapshot(organization: UUID, version: int = 1, *, status: str = "active", days: int = 30,
             valid_until: datetime | None = None, **limits: bool | int) -> plans.SnapshotDocument:
    start = datetime.now(UTC) - timedelta(days=1)
    return plans.SnapshotDocument.model_validate({
        "organizationId": str(organization), "version": version, "status": status, "planCode": "team",
        "planName": "Team",
        "periodStart": start.isoformat(), "periodEnd": (start + timedelta(days=days)).isoformat(),
        "validUntil": valid_until.isoformat() if valid_until else None, "entitlements": {**LIMITS, **limits}})


async def give_plan(db: Any, organization: UUID, **kwargs: Any) -> None:
    async with db.session() as session:
        assert await plans.apply_snapshot(session, snapshot(organization, **kwargs)) == "applied"
        await session.commit()


@pytest.mark.asyncio
async def test_with_enforcement_off_no_check_reads_or_refuses_anything() -> None:
    untouchable = Mock(side_effect=AssertionError("read while enforcement is off"))
    session = cast(Any, SimpleNamespace(get=untouchable, scalar=untouchable, execute=untouchable))
    organization = uuid4()
    assert await plans.require_new_work(session, organization) is None
    await plans.require_room(session, organization, "workers.max")
    await plans.require_feature(session, organization, "coding.enabled")
    assert await plans.require_allowance(session, organization, *plans.TASK_ALLOWANCES) is None
    assert await plans.plan_number(session, organization, "browser.sessions.concurrent") is None


@pytest.mark.asyncio
async def test_a_snapshot_applies_once_and_an_older_one_never_wins(db: Any) -> None:
    organization = uuid4()
    async with db.session() as session:
        assert await plans.apply_snapshot(session, snapshot(organization, 2)) == "applied"
        assert await plans.apply_snapshot(session, snapshot(organization, 2)) == "already_applied"
        assert await plans.apply_snapshot(session, snapshot(organization, 1)) == "stale"
        assert await plans.apply_snapshot(session, snapshot(organization, 3, status="past_due")) == "applied"
        await session.commit()
        versions = (await session.scalars(select(m.EntitlementSnapshot.version).order_by(m.EntitlementSnapshot.version))).all()
    assert versions == [2, 3]
    plan = await plans.current(None, organization)
    assert plan is not None and (plan.version, plan.status) == (3, "past_due")


def test_a_snapshot_with_a_negative_or_text_limit_is_refused() -> None:
    for value in (-1, "10", 1.5):
        with pytest.raises(ValueError):
            snapshot(uuid4(), **{"workers.max": cast(Any, value)})


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "valid_until", "allowed"), [
    ("trialing", None, True), ("active", None, True), ("past_due", datetime.now(UTC) + timedelta(days=3), True),
    ("past_due", datetime.now(UTC) - timedelta(minutes=1), False), ("expired", None, False),
    ("cancelled", None, False), ("paused", None, False),
])
async def test_status_decides_whether_new_work_may_start(db: Any, enforced: None, status: str,
                                                          valid_until: datetime | None, allowed: bool) -> None:
    organization = uuid4()
    await give_plan(db, organization, status=status, valid_until=valid_until)
    if allowed:
        assert await plans.require_new_work(None, organization) is not None
    else:
        with pytest.raises(plans.EntitlementDenied, match="new work can't start"):
            await plans.require_new_work(None, organization)


@pytest.mark.asyncio
async def test_no_plan_is_never_unlimited(db: Any, enforced: None) -> None:
    with pytest.raises(plans.EntitlementDenied, match="no plan yet") as denied:
        await plans.require_new_work(None, uuid4())
    assert denied.value.status_code == 402


@pytest.mark.asyncio
async def test_counted_limits_allow_up_to_the_plan_and_no_further(db: Any, enforced: None) -> None:
    organization, other = uuid4(), uuid4()
    await give_plan(db, organization)
    worker, user = uuid4(), uuid4()
    async with db.session() as session:
        # Archived, disconnected, the built-in web research connection, one-time jobs and other
        # organisations' rows do not count.
        session.add_all([
            m.Worker(id=worker, organization_id=organization, agent_identity_id=uuid4(), name="A"),
            m.Worker(organization_id=organization, agent_identity_id=uuid4(), name="B", status="archived"),
            m.Worker(organization_id=other, agent_identity_id=uuid4(), name="C"),
            m.Integration(organization_id=organization, provider="web_research", display_name="Web", status="connected"),
            m.Integration(organization_id=organization, provider="github", display_name="Old", status="disconnected"),
            m.OrganizationMembership(organization_id=organization, user_id=user, role="owner"),
        ])
        for name, one_shot in (("chat task", True), ("standing", False)):
            job = m.Job(id=uuid4(), organization_id=organization, worker_id=worker, name=name, status="active")
            session.add_all([job, m.JobRevision(job_id=job.id, revision=1, created_by=user, created_at=datetime.now(UTC),
                                                definition={"autonomy": {"oneShot": one_shot}})])
        await session.commit()
        await plans.require_room(session, organization, "workers.max")  # 1 of 2
        await plans.require_room(session, organization, "members.max")  # 1 of 2
        await plans.require_room(session, organization, "connections.max")  # 0 of 1
        await plans.require_room(session, organization, "storage.bytes", adding=100)
        with pytest.raises(plans.EntitlementDenied, match="includes 1 standing jobs per worker"):
            await plans.require_room(session, organization, "jobs.per_worker.max", worker_id=worker)
        with pytest.raises(plans.EntitlementDenied, match="bytes of storage"):
            await plans.require_room(session, organization, "storage.bytes", adding=101)
        session.add(m.Worker(organization_id=organization, agent_identity_id=uuid4(), name="D"))
        await session.commit()
        with pytest.raises(plans.EntitlementDenied, match="Your plan includes 2 workers"):
            await plans.require_room(session, organization, "workers.max")


@pytest.mark.asyncio
async def test_features_and_numbers_come_only_from_the_plan(db: Any, enforced: None) -> None:
    organization = uuid4()
    await give_plan(db, organization)
    await plans.require_feature(None, organization, "browser.enabled")
    with pytest.raises(plans.EntitlementDenied, match="does not include coding work"):
        await plans.require_feature(None, organization, "coding.enabled")
    with pytest.raises(plans.EntitlementDenied, match="does not include reacting to events"):
        await plans.require_feature(None, organization, "events.enabled")  # not named: not included
    assert await plans.plan_number(None, organization, "browser.sessions.concurrent") == 2


@pytest.mark.asyncio
async def test_usage_is_counted_per_period_and_allowances_refuse_at_the_limit(db: Any, enforced: None) -> None:
    organization = uuid4()
    await plans.count_usage(None, organization, tasks=5)  # no plan yet: nothing is counted
    await give_plan(db, organization)
    await plans.count_usage(None, organization, tasks=2, ai_tokens=400)
    await plans.count_usage(None, organization, ai_tokens=500, web_research=1)
    assert await plans.require_allowance(None, organization, *plans.TASK_ALLOWANCES) is not None
    await plans.count_usage(None, organization, tasks=1)
    with pytest.raises(plans.EntitlementDenied, match=r"This period's tasks \(3\) are used up. It renews on"):
        await plans.require_allowance(None, organization, *plans.TASK_ALLOWANCES)
    # A new period (the Console's next snapshot) starts new counters; the old period is kept.
    async with db.session() as session:
        later = snapshot(organization, 2).model_copy(update={"period_start": datetime.now(UTC)})
        assert await plans.apply_snapshot(session, later) == "applied"
        await session.commit()
    await plans.require_allowance(None, organization, *plans.TASK_ALLOWANCES)
    async with db.session() as session:
        rows = (await session.execute(select(m.OrganizationUsage.tasks, m.OrganizationUsage.ai_tokens,
                                      m.OrganizationUsage.web_research))).all()
    assert [tuple(row) for row in rows] == [(3, 900, 1)]


@pytest.mark.asyncio
async def test_a_saved_usage_record_counts_tasks_tokens_time_and_searches(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "usage_records_enabled", True)
    organization = uuid4()
    await give_plan(db, organization)
    with metered() as usage:
        count_plan_usage(tasks=1, web_searches=2)
    usage.ai_tokens_in, usage.ai_tokens_out, usage.browser_seconds, usage.coding_seconds = 70, 30, 4.5, 60
    await usage_records.record_usage(usage, kind="runtime.step", organization_id=organization)
    await usage_records.record_usage(Usage(), kind="runtime.step", organization_id=uuid4())  # no plan: record only
    async with db.session() as session:
        row = (await session.execute(select(m.OrganizationUsage))).scalar_one()
        records = await session.scalar(select(func.count()).select_from(m.UsageRecord))
    assert (row.tasks, row.ai_tokens, row.browser_seconds, row.coding_seconds, row.web_research) == (1, 100, 4.5, 60, 2)
    assert records == 2


@pytest.mark.asyncio
async def test_a_new_workspace_starts_on_the_newest_template(db: Any, enforced: None) -> None:
    organization = uuid4()
    template = {"version": 1, "status": "trialing", "planCode": "trial", "periodDays": 14, "validDays": 14,
                "entitlements": {"workers.max": 2, "tasks.monthly": 200, "ai.tokens.monthly": 3_000_000}}
    async with db.session() as session:
        await plans.start_from_template(session, organization)  # no template yet: no plan
        assert await plans.apply_template(session, plans.TemplateDocument.model_validate(template)) == "applied"
        assert await plans.apply_template(session, plans.TemplateDocument.model_validate(template)) == "already_applied"
        newer = {**template, "version": 2, "entitlements": {**template["entitlements"], "workers.max": 3}}
        assert await plans.apply_template(session, plans.TemplateDocument.model_validate(newer)) == "applied"
        await plans.start_from_template(session, organization)
        await session.commit()
    plan = await plans.current(None, organization)
    assert plan is not None and (plan.version, plan.status, plan.number("workers.max")) == (0, "trialing", 3)
    assert plan.valid_until is not None and plan.period_end == plan.valid_until
    assert timedelta(days=13) < plan.valid_until - datetime.now(UTC) <= timedelta(days=14)
    await plans.require_allowance(None, organization, *plans.TASK_ALLOWANCES)
    async with db.session() as session:  # the Console's first snapshot replaces the template plan
        assert await plans.apply_snapshot(session, snapshot(organization, 1)) == "applied"


@pytest.mark.asyncio
async def test_workspaces_created_before_the_first_template_start_on_it(db: Any) -> None:
    planned, unplanned = uuid4(), uuid4()
    await give_plan(db, planned)
    free = {"version": 1, "status": "active", "planCode": "free", "planName": "Free", "periodDays": 30,
            "entitlements": {"workers.max": 2}}
    async with db.session() as session:
        session.add_all([m.Organization(id=organization, name="Existing", created_by=uuid4())
                         for organization in (planned, unplanned)])
        await session.flush()
        assert await plans.apply_template(session, plans.TemplateDocument.model_validate(free)) == "applied"
        await session.commit()
    kept, started = await plans.current(None, planned), await plans.current(None, unplanned)
    assert kept is not None and (kept.version, kept.number("workers.max")) == (1, 2)
    assert started is not None and (started.version, started.valid_until) == (0, None)


@pytest.mark.asyncio
async def test_the_usage_page_shows_the_plan_its_period_and_warnings_from_the_counters(db: Any) -> None:
    organization = uuid4()
    async with db.session() as session:
        assert await plans.summary(session, organization) is None
    await give_plan(db, organization)
    await plans.count_usage(None, organization, tasks=1, ai_tokens=850)
    async with db.session() as session:
        session.add_all([m.Worker(organization_id=organization, agent_identity_id=uuid4(), name=name) for name in "AB"])
        await session.commit()
        page = await plans.summary(session, organization)
    assert page is not None
    assert (page["code"], page["name"], page["version"], page["allowsNewWork"]) == ("team", "Team", 1, True)
    limits = {item["key"]: (item["used"], item["limit"], item["state"]) for item in page["limits"]}
    assert limits["workers.max"] == (2, 2, "reached")
    assert limits["ai.tokens.monthly"] == (850, 1000, "near")
    assert limits["tasks.monthly"] == (1, 3, "ok")
    assert limits["jobs.per_worker.max"] == (0, 1, "ok")
    assert {item["key"]: item["included"] for item in page["included"]} == {
        "browser.enabled": True, "coding.enabled": False, "events.enabled": False}
    assert page["periodEnd"] is not None


@pytest.mark.asyncio
async def test_a_free_plan_never_expires_and_its_usage_resets_each_period(
        db: Any, enforced: None, monkeypatch: pytest.MonkeyPatch) -> None:
    organization = uuid4()
    free = {"version": 1, "status": "active", "planCode": "free", "periodDays": 30,
            "entitlements": {"tasks.monthly": 1, "ai.tokens.monthly": 3_000_000}}
    async with db.session() as session:
        await plans.apply_template(session, plans.TemplateDocument.model_validate(free))
        await plans.start_from_template(session, organization)
        await session.commit()
    plan = await plans.current(None, organization)
    assert plan is not None and plan.valid_until is None and plan.status == "active"
    await plans.count_usage(None, organization, tasks=1)
    with pytest.raises(plans.EntitlementDenied, match="used up"):
        await plans.require_allowance(None, organization, *plans.TASK_ALLOWANCES)
    # Months later, with no newer plan from the Console: still allowed, in a fresh period.
    later = datetime.now(UTC) + timedelta(days=95)
    start, end = plan.period_at(later)
    assert start == plan.period_start + timedelta(days=90) and end == start + timedelta(days=30)
    assert plan.allows_new_work(later)
    clock = Mock(wraps=datetime)
    clock.now = Mock(return_value=later)
    monkeypatch.setattr(plans, "datetime", clock)
    await plans.require_allowance(None, organization, *plans.TASK_ALLOWANCES)
    await plans.count_usage(None, organization, tasks=1)
    async with db.session() as session:
        periods = (await session.execute(select(m.OrganizationUsage.period_start, m.OrganizationUsage.tasks)
                                         .order_by(m.OrganizationUsage.period_start))).all()
    assert [row.tasks for row in periods] == [1, 1]


@pytest.mark.asyncio
async def test_a_task_the_plan_refuses_ends_without_running_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.internal import runtime

    monkeypatch.setattr(runtime.entitlements, "require_allowance",
                        AsyncMock(side_effect=plans.EntitlementDenied("This period's tasks (3) are used up.", "tasks.monthly")))
    admit, notify = AsyncMock(return_value=True), AsyncMock()
    monkeypatch.setattr(runtime, "admit_due_item", admit)
    monkeypatch.setattr(runtime, "notify_task_conversation", notify)
    session = SimpleNamespace(commit=AsyncMock())
    item = SimpleNamespace(id=uuid4(), organization_id=uuid4(), status="queued", payload={},
                           scheduled_at=datetime.now(UTC) - timedelta(seconds=1))
    message = SimpleNamespace(expected_step=0)
    result = await runtime._not_runnable(session, cast(Any, item), cast(Any, message))
    assert result is not None and result["failureCategory"] == "plan_limit"
    assert item.status == "failed" and item.payload["runtime"]["queueState"] == "not_started"
    admit.assert_not_awaited()
    assert notify.await_args is not None
    assert notify.await_args.kwargs["content"] == "This period's tasks (3) are used up."


@pytest.mark.asyncio
async def test_a_task_already_under_way_is_never_stopped_by_a_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.internal import runtime

    refuse = AsyncMock(side_effect=plans.EntitlementDenied("used up", "tasks.monthly"))
    monkeypatch.setattr(runtime.entitlements, "require_allowance", refuse)
    monkeypatch.setattr(runtime, "admit_due_item", AsyncMock(return_value=True))
    session = SimpleNamespace(commit=AsyncMock())
    for marker in ("startedAt", "admittedAt"):
        item = SimpleNamespace(id=uuid4(), organization_id=uuid4(), status="queued",
                               payload={"runtime": {marker: "2026-10-16T10:00:00+00:00"}},
                               scheduled_at=datetime.now(UTC) - timedelta(seconds=1))
        with metered() as usage:
            assert await runtime._not_runnable(session, cast(Any, item), cast(Any, SimpleNamespace(expected_step=3))) is None
        assert item.status == "queued" and usage.tasks == 0  # admitted again, not counted again
    refuse.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_new_task_is_counted_once_when_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.internal import runtime

    monkeypatch.setattr(runtime, "admit_due_item", AsyncMock(return_value=True))
    item = SimpleNamespace(id=uuid4(), organization_id=uuid4(), status="queued", payload={},
                           scheduled_at=datetime.now(UTC) - timedelta(seconds=1))
    with metered() as usage:
        await runtime._not_runnable(SimpleNamespace(commit=AsyncMock()), cast(Any, item),
                                    cast(Any, SimpleNamespace(expected_step=0)))
    assert usage.tasks == 1 and item.payload["runtime"]["admittedAt"]


@pytest.mark.asyncio
async def test_a_schedule_runs_no_more_often_than_the_plan_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import jobs_routes

    monkeypatch.setattr(jobs_routes.entitlements, "plan_number", AsyncMock(return_value=15))
    session, organization = cast(Any, object()), uuid4()
    await jobs_routes._require_plan_interval(session, organization, {"enabled": True, "cadence": "interval",
                                                                      "intervalMinutes": 15})
    await jobs_routes._require_plan_interval(session, organization, {"enabled": True, "cadence": "daily"})
    with pytest.raises(plans.EntitlementDenied, match="at most every 15 minutes"):
        await jobs_routes._require_plan_interval(session, organization, {"enabled": True, "cadence": "interval",
                                                                          "intervalMinutes": 5})


@pytest.mark.asyncio
async def test_the_plan_sets_concurrent_browser_sessions_and_can_exclude_browser_work(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from test_f30_0_10 import FakeBrowserRuntime, request_for

    from app.execution.contracts import ExecutionProviderError
    from app.execution.providers.browser import PlaywrightBrowserProvider

    provider = PlaywrightBrowserProvider(FakeBrowserRuntime())
    request = request_for(provider, operation="navigation.open", payload={"url": "https://example.com"})
    assert await provider._plan_sessions(request) is None  # not enforced: the server's setting applies
    monkeypatch.setattr(plans, "require_feature", AsyncMock())
    monkeypatch.setattr(plans, "plan_number", AsyncMock(return_value=3))
    assert await provider._plan_sessions(request) == 3
    monkeypatch.setattr(plans, "require_feature", AsyncMock(side_effect=plans.EntitlementDenied("no browser", "browser.enabled")))
    with pytest.raises(ExecutionProviderError) as refused:
        await provider._plan_sessions(request)
    assert refused.value.error.code == "policy_blocked" and not refused.value.error.retryable


@pytest.mark.asyncio
async def test_a_one_time_task_needs_no_room_for_a_standing_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import jobs_routes

    room = AsyncMock(side_effect=plans.EntitlementDenied("Your plan includes 1 standing jobs per worker.", "jobs.per_worker.max"))
    monkeypatch.setattr(jobs_routes.entitlements, "require_room", room)
    worker, organization = uuid4(), uuid4()
    session = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(id=worker)), add=Mock(), flush=AsyncMock(),
                              commit=AsyncMock(), refresh=AsyncMock())
    principal = SimpleNamespace(user_id=uuid4(), permissions=frozenset({"jobs.manage"}))
    monkeypatch.setattr(jobs_routes, "require_permission", Mock())
    monkeypatch.setattr(jobs_routes, "append_audit", AsyncMock())
    await jobs_routes.create_job(cast(Any, session), organization, cast(Any, principal),
                                 {"workerId": str(worker), "name": "One-time task", "autonomy": {"oneShot": True}})
    room.assert_not_awaited()
    with pytest.raises(plans.EntitlementDenied):
        await jobs_routes.create_job(cast(Any, session), organization, cast(Any, principal),
                                     {"workerId": str(worker), "name": "Weekly report"})


@pytest.mark.asyncio
async def test_a_chat_message_is_refused_before_any_work_when_the_plan_does_not_allow_it(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import conversation_routes

    monkeypatch.setattr(conversation_routes.entitlements, "require_allowance",
                        AsyncMock(side_effect=plans.EntitlementDenied("This workspace's plan is expired.", "plan.status")))
    reply = AsyncMock()
    with pytest.raises(plans.EntitlementDenied) as refused:
        await conversation_routes._replied(cast(Any, object()), uuid4(), uuid4(), reply)
    assert refused.value.status_code == 402
    reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lost_plan_is_applied_again_without_duplicating_its_history(db: Any) -> None:
    organization = uuid4()
    await give_plan(db, organization, version=5)
    async with db.session() as session:
        await session.execute(delete(m.OrganizationEntitlement))  # the current plan is lost; history remains
        await session.commit()
    plans.forget()
    async with db.session() as session:
        assert await plans.apply_snapshot(session, snapshot(organization, 5)) == "applied"
        await session.commit()
        history = (await session.scalars(select(m.EntitlementSnapshot.version))).all()
    assert history == [5]
    plan = await plans.current(None, organization)
    assert plan is not None and plan.version == 5


def test_the_plans_comparison_names_every_shown_limit_once() -> None:
    rows = plans.comparison()
    keys = [row["key"] for row in rows]
    assert len(keys) == len(set(keys)) and keys[0] == "workers.max"
    assert {row["kind"] for row in rows} == {"count", "period", "feature", "setting"}
    assert all(row["name"] for row in rows)
