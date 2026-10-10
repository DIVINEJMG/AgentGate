from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.jobs_routes import work_item_public
from app.api.runtime_result_routes import _step_public, _step_v2
from app.infrastructure.database.models import RunStep, WorkItem


@pytest.mark.parametrize("runtime", [None, [], "invalid", {}, {"providerRetryAt": "later"}])
async def test_queue_status_handles_absent_or_malformed_runtime_evidence(runtime):
    now = datetime.now(UTC)
    item = WorkItem(id=uuid4(), organization_id=uuid4(), job_id=uuid4(),
        job_revision_id=uuid4(), priority="normal", status="queued",
        payload={"runtime": runtime}, correlation_id="test", scheduled_at=now,
        created_at=now, updated_at=now)
    session = cast(AsyncSession, SimpleNamespace(get=AsyncMock(return_value=None),
                                               scalar=AsyncMock(return_value=None)))
    public = await work_item_public(session, item)
    assert public["queueState"] == ("retrying" if isinstance(runtime, dict) and runtime.get("providerRetryAt") else "queued")
    assert public["waitingReason"] is None


def test_browser_no_progress_is_visible_without_changing_execution_status() -> None:
    step = SimpleNamespace(
        id=uuid4(),
        run_id=uuid4(),
        step_index=3,
        kind="action",
        status="completed",
        input={"title": "Press Enter", "scope": "browser.element.press_key"},
        output={
            "data": {
                "output": {
                    "actionEvidence": {
                        "result": "executed",
                        "outcome": "no_observable_change",
                    }
                }
            }
        },
    )

    public = _step_public(cast(RunStep, step))
    v2 = _step_v2(public)

    assert public["status"] == "completed"
    assert public["actionOutcome"] == "no_observable_change"
    assert v2["identity"]["actionOutcome"] == "no_observable_change"
