"""Improvement plan, phase 2: schedules start on time, the sweep reads every schedule in one query,
and time zones, clock changes and missed runs behave as documented."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import jobs_routes
from app.api.internal import runtime
from app.bootstrap.settings import settings
from app.infrastructure.database import models as m
from app.infrastructure.database.base import Base
from app.runtime import schedule_wake
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes

NOW = datetime(2026, 10, 13, 9, 0, 30, tzinfo=UTC)
ORG = uuid4()


# ---------- the next run: time zones and clock changes ----------


@pytest.mark.parametrize(("zone", "local_time"), [
    ("America/New_York", "02:30"),   # missing on the spring-forward night
    ("America/New_York", "01:30"),   # happens twice on the fall-back night
    ("Europe/London", "01:30"),
    ("Australia/Lord_Howe", "02:15"),  # half-hour clock changes
    ("Africa/Lagos", "09:00"),
])
def test_a_daily_schedule_runs_once_every_local_day_across_clock_changes(zone: str, local_time: str) -> None:
    schedule = {"cadence": "daily", "timezone": zone, "localTime": local_time}
    current, days, gaps = datetime(2026, 1, 1, tzinfo=UTC), [], set()
    for index in range(366):
        following = jobs_routes._next_schedule_occurrence(schedule, current)
        assert following > current
        if index:
            gaps.add((following - current).total_seconds() / 3600)
        days.append(following.astimezone(ZoneInfo(zone)).date())
        current = following
    assert len(set(days)) == len(days) == (days[-1] - days[0]).days + 1
    assert gaps <= {23.0, 23.5, 24.0, 24.5, 25.0}


def test_a_missing_local_time_runs_at_the_same_clock_reading_after_the_change() -> None:
    schedule = {"cadence": "daily", "timezone": "America/New_York", "localTime": "02:30"}
    # 8 March 2026: clocks go from 02:00 to 03:00, so 02:30 does not exist; it runs at 03:30.
    run = jobs_routes._next_schedule_occurrence(schedule, datetime(2026, 3, 8, 5, 0, tzinfo=UTC))
    assert run == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    assert run.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M") == "03:30"


def test_weekly_and_interval_schedules() -> None:
    weekly = {"cadence": "weekly", "timezone": "Africa/Lagos", "localTime": "08:00", "weekdays": ["monday", "thursday"]}
    current, runs = datetime(2026, 10, 12, 0, 0, tzinfo=UTC), []  # a Monday
    for _ in range(4):
        current = jobs_routes._next_schedule_occurrence(weekly, current)
        runs.append(current.astimezone(ZoneInfo("Africa/Lagos")).strftime("%a %H:%M"))
    assert runs == ["Mon 08:00", "Thu 08:00", "Mon 08:00", "Thu 08:00"]
    interval = {"cadence": "interval", "intervalMinutes": 2}  # at least five minutes
    assert jobs_routes._next_schedule_occurrence(interval, NOW) == NOW + timedelta(minutes=5)


# ---------- the sweep: one query, every schedule, each run once ----------


class Scheduler:
    """Scheduled jobs in SQLite, with queueing and wake-ups recorded instead of performed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(self.engine, tables=[cast(Any, model.__table__) for model in (m.Worker, m.Job, m.JobRevision)])
        self.statements: list[str] = []
        event.listen(self.engine, "before_cursor_execute", lambda conn, cursor, statement, *rest: self.statements.append(statement))
        self.queued: list[dict[str, Any]] = []
        self.armed: list[tuple[UUID, datetime]] = []
        self.worker = uuid4()
        with Session(self.engine) as session:
            session.add(m.Worker(id=self.worker, organization_id=ORG, agent_identity_id=uuid4(), name="Ada",
                                 department="", status="active", profile={}))
            session.commit()

        async def queue_job(session: Any, organization_id: Any, job: Any, principal: Any, *, trigger: Any, scheduled_at: Any) -> None:
            self.queued.append({"job": job.id, "trigger": trigger, "scheduled_at": scheduled_at})
            await session.commit()

        async def arm(*, organization_id: Any, job_id: UUID, due: datetime, now: Any = None) -> None:
            self.armed.append((job_id, due))

        monkeypatch.setattr(jobs_routes, "queue_job", queue_job)
        monkeypatch.setattr(jobs_routes, "arm_schedule", arm)

    def job(self, schedule: dict[str, Any] | None, *, updated: datetime = NOW, status: str = "active") -> UUID:
        job_id = uuid4()
        with Session(self.engine) as session:
            session.add(m.Job(id=job_id, organization_id=ORG, worker_id=self.worker, name="Report", status=status,
                              current_revision=1, created_at=updated, updated_at=updated))
            definition: dict[str, Any] = {"objective": "x" * 2_000}
            if schedule is not None:
                definition["triggerConfig"] = {"id": f"config-{job_id}", "schedule": schedule}
            session.add(m.JobRevision(job_id=job_id, revision=1, definition=definition, created_by=uuid4(), created_at=updated))
            session.commit()
        return job_id

    def schedule(self, job_id: UUID) -> dict[str, Any]:
        with Session(self.engine) as session:
            revision = session.scalar(select(m.JobRevision).where(m.JobRevision.job_id == job_id))
            assert revision is not None
            return revision.definition["triggerConfig"]["schedule"]

    async def sweep(self, now: datetime = NOW, **kwargs: Any) -> int:
        with _aware_sqlite_datetimes():
            async with BenchSession(Session(self.engine, expire_on_commit=False)) as session:
                return await jobs_routes.queue_due_schedules(cast(Any, session), now=now, **kwargs)


def _daily(due: datetime, **extra: Any) -> dict[str, Any]:
    return {"enabled": True, "cadence": "daily", "timezone": "UTC", "localTime": "09:00",
            "nextDueAt": due.isoformat(), **extra}


@pytest.mark.asyncio
async def test_every_scheduled_job_is_considered_not_only_the_first_two_hundred(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = Scheduler(monkeypatch)
    for index in range(210):  # older, unscheduled jobs used to fill the 200-job window
        scheduler.job(None, updated=NOW - timedelta(days=30, minutes=index))
    due = scheduler.job(_daily(NOW - timedelta(seconds=30)))
    assert await scheduler.sweep() == 1
    assert [entry["job"] for entry in scheduler.queued] == [due]


@pytest.mark.asyncio
async def test_the_sweep_reads_schedules_in_one_query_whatever_the_number_of_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    counts = []
    for jobs in (3, 30):
        scheduler = Scheduler(monkeypatch)
        for _ in range(jobs):
            scheduler.job(_daily(NOW + timedelta(hours=1)))
        scheduler.statements.clear()
        assert await scheduler.sweep() == 0
        [statement] = scheduler.statements
        assert "job_revisions.definition" in statement  # only the schedule settings, by JSON path
        counts.append(len(scheduler.statements))
    assert counts == [1, 1]


@pytest.mark.asyncio
async def test_a_due_run_is_queued_once_and_the_next_one_armed(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = Scheduler(monkeypatch)
    due = NOW - timedelta(seconds=30)
    job_id = scheduler.job(_daily(due))
    assert await scheduler.sweep() == 1
    assert await scheduler.sweep() == 0  # a second sweep or wake-up finds it already queued
    [entry] = scheduler.queued
    assert entry["trigger"]["dedupeKey"] == f"schedule:config-{job_id}:{due.isoformat()}"
    tomorrow = due + timedelta(days=1)
    assert scheduler.schedule(job_id)["nextDueAt"] == tomorrow.isoformat()
    assert scheduler.armed == [(job_id, tomorrow)]
    assert scheduler.schedule(job_id)["lastQueued"]["dueAt"] == due.isoformat()


@pytest.mark.asyncio
async def test_queue_once_queues_one_run_for_a_missed_period_then_resumes_from_now(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = Scheduler(monkeypatch)
    job_id = scheduler.job(_daily(NOW - timedelta(days=3, seconds=30)))
    assert await scheduler.sweep() == 1
    assert await scheduler.sweep() == 0  # previously each sweep queued another missed day
    assert datetime.fromisoformat(scheduler.schedule(job_id)["nextDueAt"]) == NOW.replace(second=0) + timedelta(days=1)


@pytest.mark.asyncio
async def test_skip_queues_nothing_and_records_why(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = Scheduler(monkeypatch)
    missed = NOW - timedelta(days=2, seconds=30)
    job_id = scheduler.job(_daily(missed, missedRunPolicy="skip"))
    assert await scheduler.sweep() == 0 and scheduler.queued == []
    schedule = scheduler.schedule(job_id)
    assert schedule["lastSkipped"]["dueAt"] == missed.isoformat()
    assert datetime.fromisoformat(schedule["nextDueAt"]) > NOW
    assert scheduler.armed == [(job_id, datetime.fromisoformat(schedule["nextDueAt"]))]


@pytest.mark.asyncio
async def test_a_wake_up_queues_only_its_own_job_and_ignores_paused_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = Scheduler(monkeypatch)
    mine = scheduler.job(_daily(NOW - timedelta(seconds=30)))
    other = scheduler.job(_daily(NOW - timedelta(seconds=30)))
    paused = scheduler.job(_daily(NOW - timedelta(seconds=30)), status="paused")
    assert await scheduler.sweep(limit=1, job_id=mine) == 1
    assert await scheduler.sweep(limit=1, job_id=paused) == 0
    assert [entry["job"] for entry in scheduler.queued] == [mine]
    assert await scheduler.sweep() == 1 and scheduler.queued[-1]["job"] == other


# ---------- on-time wake-ups ----------


class _QStash:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return SimpleNamespace(id=f"m{len(self.sent)}")


@pytest.mark.asyncio
async def test_a_wake_up_is_delayed_to_the_due_time_in_hops_qstash_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    qstash = _QStash()
    monkeypatch.setattr(settings, "qstash_runtime_execute_url", "https://api.example/internal/v1/runtime/execute")
    monkeypatch.setattr(settings, "qstash_url", "https://qstash.example")
    monkeypatch.setattr(settings, "qstash_token", cast(Any, "t"))
    monkeypatch.setattr(settings, "qstash_max_delay_seconds", 7 * 86_400)
    monkeypatch.setattr(schedule_wake.UpstashQStashProvider, "from_settings", lambda: qstash)
    job_id = uuid4()
    soon, far = NOW + timedelta(hours=3), NOW + timedelta(days=10)
    for due in (soon, soon, far):
        await schedule_wake.arm_schedule(organization_id=ORG, job_id=job_id, due=due, now=NOW)
    first, again, hop = qstash.sent
    assert first["destination"] == "https://api.example/internal/v1/runtime/schedule-due"
    assert first["delay_seconds"] == 3 * 3600 and json.loads(first["body"])["due"] == soon.isoformat()
    assert again["idempotency_key"] == first["idempotency_key"]  # arming twice is deduplicated
    assert hop["delay_seconds"] == 7 * 86_400 and hop["idempotency_key"] != first["idempotency_key"]


@pytest.mark.asyncio
async def test_a_failed_wake_up_is_left_to_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Down:
        async def publish(self, **kwargs: Any) -> Any:
            raise RuntimeError("QStash unavailable")

    monkeypatch.setattr(settings, "qstash_runtime_execute_url", "https://api.example/internal/v1/runtime/execute")
    monkeypatch.setattr(settings, "qstash_url", "https://qstash.example")
    monkeypatch.setattr(settings, "qstash_token", cast(Any, "t"))
    monkeypatch.setattr(schedule_wake.UpstashQStashProvider, "from_settings", lambda: _Down())
    assert await schedule_wake.arm_schedule(organization_id=ORG, job_id=uuid4(), due=NOW, now=NOW) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("seconds_early", "expected"), [(3_600, "rearmed"), (5, "ok"), (-2, "ok")])
async def test_the_wake_up_endpoint(monkeypatch: pytest.MonkeyPatch, seconds_early: int, expected: str) -> None:
    import httpx
    from fastapi import FastAPI

    calls: dict[str, Any] = {"queued": [], "armed": [], "slept": []}

    async def queue(session: Any, *, now: datetime, limit: int, job_id: UUID) -> int:
        calls["queued"].append((limit, job_id))
        return 1

    async def arm(**kwargs: Any) -> None:
        calls["armed"].append(kwargs)

    async def sleep(seconds: float) -> None:
        calls["slept"].append(seconds)

    class _Session:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(settings, "runtime_execution_enabled", True)
    monkeypatch.setattr(runtime, "_verify_qstash", lambda *a: None)
    monkeypatch.setattr(runtime, "queue_due_schedules", queue)
    monkeypatch.setattr(runtime, "session_factory", lambda: _Session())
    monkeypatch.setattr(schedule_wake, "arm_schedule", arm)
    monkeypatch.setattr(runtime.asyncio, "sleep", sleep)
    app = FastAPI()
    app.include_router(runtime.router)
    job_id = uuid4()
    due = datetime.now(UTC) + timedelta(seconds=seconds_early)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        response = await client.post("/internal/v1/runtime/schedule-due", headers={"Upstash-Signature": "s"},
                                     json={"organization_id": str(ORG), "job_id": str(job_id), "due": due.isoformat()})
    assert response.status_code == 200 and response.json()["status"] == expected
    if expected == "rearmed":
        assert calls["queued"] == [] and calls["armed"][0]["due"] == due
    else:
        assert calls["queued"] == [(1, job_id)]
        assert bool(calls["slept"]) == (seconds_early > 0)
