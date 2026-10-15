"""The daily coding budget follows the sessions: a task still working keeps its reservation, a
task that ended counts only what it used, and a reservation no session holds is given back. Found
live: finished tasks' paused sessions and orphan reservations held 7,200 of 7,200 seconds while
about 1,240 had been used, so coding stopped for the day."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.bootstrap.settings import settings
from app.execution.coding import service
from app.execution.contracts import ExecutionProviderError
from app.infrastructure.database import models as m
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes
from tests.test_approvals_phase4 import TABLES
from tests.test_efficiency_queries import ORG, Database

DAY = datetime.now(UTC).date().isoformat()
REQUEST = SimpleNamespace(organization_id=ORG, operation="repository.workspace.open", correlation_id="c")


class Budget:
    def __init__(self, reserved: int) -> None:
        self.db = Database(TABLES)
        self.row = self.db.add(m.CodingBudget, organization_id=ORG, day=DAY, reserved_seconds=reserved)

    def session_for(self, item_status: str, session_status: str, active: int, **evidence: Any) -> None:
        item = self.db.add(m.WorkItem, organization_id=ORG, job_id=uuid4(), job_revision_id=uuid4(), status=item_status)
        now = datetime.now(UTC)
        self.db.add(m.CodingSession, organization_id=ORG, work_item_id=item.id, resource_id=uuid4(), base_sha="a" * 40,
                    status=session_status, active_seconds=active, last_activity_at=now, expires_at=now,
                    evidence={"budgetDay": DAY, "reservedSeconds": 1800, **evidence})

    async def run(self, call: Any) -> Any:
        with _aware_sqlite_datetimes():
            session = BenchSession(Session(self.db.engine, expire_on_commit=False))
            return await call(cast(Any, session))


@pytest.mark.asyncio
async def test_ended_tasks_count_only_what_they_used() -> None:
    budget = Budget(reserved=7200)
    budget.session_for("partial_completion", "paused", 486)
    budget.session_for("partial_completion", "paused", 755)
    budget.session_for("running", "running", 100)
    budget.session_for("completed", "closed", 900, budgetReleased=True)
    total = await budget.run(lambda s: service.committed_seconds(s, ORG, DAY))
    # 486 + 755 used, the running task keeps 1800, the closed one used 900.
    assert total == 486 + 755 + 1800 + 900


@pytest.mark.asyncio
async def test_a_leaked_budget_no_longer_stops_coding(monkeypatch: pytest.MonkeyPatch) -> None:
    async def admitted(*_: Any) -> None:
        return None

    monkeypatch.setattr(service, "admit_capacity", admitted)
    budget = Budget(reserved=7200)  # what the live run found: full, with only 1,241 s used
    budget.session_for("partial_completion", "paused", 486)
    budget.session_for("partial_completion", "paused", 755)
    async def reserve_and_commit(session: Any) -> str:
        day = await service.reserve(session, REQUEST)
        await session.commit()
        return day

    assert await budget.run(reserve_and_commit) == DAY

    async def saved(session: Any) -> int:
        row = await session.get(m.CodingBudget, budget.row.id)
        return row.reserved_seconds

    assert await budget.run(saved) == 486 + 755 + settings.coding_task_active_seconds


@pytest.mark.asyncio
async def test_working_tasks_still_fill_the_day(monkeypatch: pytest.MonkeyPatch) -> None:
    async def admitted(*_: Any) -> None:
        return None

    monkeypatch.setattr(service, "admit_capacity", admitted)
    budget = Budget(reserved=0)
    for _ in range(settings.coding_organization_daily_seconds // settings.coding_task_active_seconds):
        budget.session_for("running", "running", 0)
    with pytest.raises(ExecutionProviderError, match="budget is exhausted"):
        await budget.run(lambda s: service.reserve(s, REQUEST))
