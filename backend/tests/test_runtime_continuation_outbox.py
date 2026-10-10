from __future__ import annotations

from types import SimpleNamespace
from typing import Self
from uuid import uuid4

import pytest

from app.runtime import outbox_publisher
from app.runtime.qstash_trigger import RuntimeSignalResult


class _Session:
    def __init__(self) -> None:
        self.committed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


class _Outbox:
    def __init__(self, _session: _Session, *events: SimpleNamespace) -> None:
        self.events = events
        self.published: list[SimpleNamespace] = []
        self.failed: list[SimpleNamespace] = []

    async def unpublished(self, *, limit: int) -> list[SimpleNamespace]:
        del limit
        return list(self.events)

    async def mark_published(self, event: SimpleNamespace) -> None:
        assert event in self.events
        self.published.append(event)

    async def mark_failed(self, event: SimpleNamespace) -> None:
        assert event in self.events
        self.failed.append(event)


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_run_progress_outbox_recovers_planner_continuation(
    monkeypatch: pytest.MonkeyPatch, accepted: bool
) -> None:
    organization_id = uuid4()
    work_item_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        topic="run.progress",
        aggregate_id=str(uuid4()),
        payload={
            "organization_id": str(organization_id),
            "work_item_id": str(work_item_id),
            "current_step": 3,
            "continuation_phase": "plan",
        },
    )
    session = _Session()
    outbox = _Outbox(session, event)
    calls: list[dict[str, object]] = []

    async def publish(**kwargs: object) -> RuntimeSignalResult:
        calls.append(kwargs)
        return RuntimeSignalResult(
            message_id="queued-message" if accepted else None,
            state="queued" if accepted else "unavailable",
            detail=None,
            updated_at="2026-10-05T00:00:00Z",
        )

    async def realtime(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(outbox_publisher, "session_factory", lambda: session)
    monkeypatch.setattr(outbox_publisher, "TransactionalOutbox", lambda s: outbox)
    monkeypatch.setattr(outbox_publisher, "request_runtime_execution_detailed", publish)
    monkeypatch.setattr(outbox_publisher, "_publish_realtime", realtime)
    monkeypatch.setattr(outbox_publisher, "web_research_destination", lambda: None)

    if accepted:
        assert await outbox_publisher.drain_outbox_batch() == 1
        assert outbox.published == [event]
        assert session.committed is True
    else:
        assert await outbox_publisher.drain_outbox_batch() == 0
        assert outbox.failed == [event]
        assert outbox.published == []
        assert session.committed is True

    assert calls == [{
        "organization_id": organization_id,
        "work_item_id": work_item_id,
        "expected_step": 3,
        "reason": "continuation-plan",
    }]


@pytest.mark.asyncio
async def test_unconfigured_research_event_cannot_block_runtime_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    work_item_id = uuid4()
    research = SimpleNamespace(
        id=uuid4(), topic="web.research.queued", aggregate_id=str(uuid4()), payload={}
    )
    progress = SimpleNamespace(
        id=uuid4(), topic="run.progress", aggregate_id=str(uuid4()),
        payload={
            "organization_id": str(organization_id),
            "work_item_id": str(work_item_id),
            "current_step": 3,
            "continuation_phase": "plan",
        },
    )
    session = _Session()
    outbox = _Outbox(session, research, progress)
    calls: list[dict[str, object]] = []

    async def publish(**kwargs: object) -> RuntimeSignalResult:
        calls.append(kwargs)
        return RuntimeSignalResult(
            message_id="queued-message", state="queued", detail=None,
            updated_at="2026-10-05T00:00:00Z",
        )

    async def realtime(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(outbox_publisher, "session_factory", lambda: session)
    monkeypatch.setattr(outbox_publisher, "TransactionalOutbox", lambda s: outbox)
    monkeypatch.setattr(outbox_publisher, "request_runtime_execution_detailed", publish)
    monkeypatch.setattr(outbox_publisher, "_publish_realtime", realtime)
    monkeypatch.setattr(outbox_publisher, "web_research_destination", lambda: None)

    assert await outbox_publisher.drain_outbox_batch() == 1
    assert outbox.published == [progress]
    assert session.committed is True
    assert calls[0]["work_item_id"] == work_item_id


@pytest.mark.asyncio
async def test_realtime_outage_does_not_prevent_runtime_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    work_item_id = uuid4()
    progress = SimpleNamespace(
        id=uuid4(), topic="run.progress", aggregate_id=str(uuid4()),
        payload={
            "organization_id": str(organization_id),
            "work_item_id": str(work_item_id),
            "current_step": 3,
            "continuation_phase": "plan",
        },
    )
    session = _Session()
    outbox = _Outbox(session, progress)
    dispatched: list[dict[str, object]] = []

    async def publish(**kwargs: object) -> RuntimeSignalResult:
        dispatched.append(kwargs)
        return RuntimeSignalResult(
            message_id="queued-message", state="queued", detail=None,
            updated_at="2026-10-05T00:00:00Z",
        )

    async def realtime(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("Redis unavailable")

    monkeypatch.setattr(outbox_publisher, "session_factory", lambda: session)
    monkeypatch.setattr(outbox_publisher, "TransactionalOutbox", lambda s: outbox)
    monkeypatch.setattr(outbox_publisher, "request_runtime_execution_detailed", publish)
    monkeypatch.setattr(outbox_publisher, "_publish_realtime", realtime)
    monkeypatch.setattr(outbox_publisher, "web_research_destination", lambda: None)

    assert await outbox_publisher.drain_outbox_batch() == 0
    assert dispatched[0]["work_item_id"] == work_item_id
    assert outbox.published == []
