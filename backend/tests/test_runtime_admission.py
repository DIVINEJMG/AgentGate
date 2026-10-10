from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from app.infrastructure.database.models import WorkItem
from app.runtime.admission import admission_order


def _item(organization, priority, created_at):
    return SimpleNamespace(
        id=uuid4(), organization_id=organization,
        priority=priority, created_at=created_at,
    )


def test_old_normal_work_eventually_overtakes_new_urgent_work():
    now = datetime.now(UTC)
    old = _item(uuid4(), "normal", now - timedelta(minutes=30))
    urgent = _item(uuid4(), "urgent", now)

    assert admission_order(
        cast(list[WorkItem], [urgent, old]), now=now, active_by_organization=Counter()
    )[0] is old


def test_active_organization_yields_to_equivalent_waiting_organization():
    now = datetime.now(UTC)
    active_org = uuid4()
    busy = _item(active_org, "normal", now)
    idle = _item(uuid4(), "normal", now)

    assert admission_order(
        cast(list[WorkItem], [busy, idle]), now=now, active_by_organization=Counter({active_org: 1})
    )[0] is idle
