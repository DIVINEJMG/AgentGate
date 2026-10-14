"""QStash is billed per message. A serving backend drains its own outbox after a commit (no
QStash round trip), GitHub events for repositories no task is connected to are recorded as
unmatched without a queue message, and every QStash message is counted, scheduled deliveries
and publishes outside measured work included."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.sql.dml import Insert

from app.bootstrap.settings import settings
from app.execution.providers.native.github import event_routing
from app.infrastructure.database import usage_records
from app.observability.usage import Usage, count_qstash_message, metered
from app.runtime import outbox_publisher, outbox_trigger

# --- Draining in the server --------------------------------------------------------------------


class Flags:
    def __init__(self, held: bool = False) -> None:
        self.held = held
        self.cleared = 0

    def __call__(self) -> Flags:
        return self

    async def claim_flag(self, _key: str, *, ttl_seconds: int) -> bool:
        if self.held:
            return False
        self.held = True
        return True

    async def clear_flag(self, _key: str) -> None:
        self.held, self.cleared = False, self.cleared + 1

    async def close(self) -> None:
        return None


@pytest.fixture
def drain(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    flags = Flags()
    seen: dict[str, Any] = {"batches": [], "published": 0, "recorded": []}
    batches = iter([100, 3])

    async def batch(*, limit: int) -> int:
        seen["batches"].append(limit)
        return next(batches)

    async def publish(**_: Any) -> Any:
        seen["published"] += 1
        return SimpleNamespace(id="m")

    async def record(usage: Usage, **fields: Any) -> None:
        seen["recorded"].append(fields["kind"])

    monkeypatch.setattr(outbox_trigger.RedisCoordinator, "from_settings", flags)
    monkeypatch.setattr(outbox_publisher, "drain_outbox_batch", batch)
    monkeypatch.setattr(usage_records, "record_usage", record)
    monkeypatch.setattr(outbox_trigger.UpstashQStashProvider, "from_settings", lambda: SimpleNamespace(publish=publish))
    monkeypatch.setattr(settings, "outbox_drain_local", True)
    monkeypatch.setattr(settings, "qstash_outbox_drain_url", "https://api.example/internal/v1/outbox/drain")
    monkeypatch.setattr(settings, "qstash_url", "https://qstash.example")
    monkeypatch.setattr(settings, "qstash_token", SimpleNamespace(get_secret_value=lambda: "t"))
    seen["flags"] = flags
    return seen


@pytest.mark.asyncio
async def test_a_serving_backend_drains_here_without_a_qstash_message(drain: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbox_trigger, "serving", True)
    assert await outbox_trigger.request_outbox_drain(reason="transaction_committed") == outbox_trigger.DRAINED_HERE
    assert drain["published"] == 0
    # A full batch is followed by another; the marker is cleared before each read.
    assert drain["batches"] == [100, 100] and drain["flags"].cleared == 2
    assert drain["recorded"] == ["outbox.drain"]


@pytest.mark.asyncio
async def test_commits_while_a_drain_is_on_its_way_rely_on_it(drain: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbox_trigger, "serving", True)
    drain["flags"].held = True
    assert await outbox_trigger.request_outbox_drain(reason="transaction_committed") == outbox_trigger.COALESCED
    assert drain["batches"] == []


@pytest.mark.asyncio
async def test_outside_the_server_the_qstash_drain_request_is_unchanged(drain: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbox_trigger, "serving", False)
    assert await outbox_trigger.request_outbox_drain(reason="transaction_committed") == "m"
    assert drain["published"] == 1 and drain["batches"] == []


@pytest.mark.asyncio
async def test_the_setting_turns_local_draining_off(drain: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbox_trigger, "serving", True)
    monkeypatch.setattr(settings, "outbox_drain_local", False)
    assert await outbox_trigger.request_outbox_drain(reason="transaction_committed") == "m"
    assert drain["batches"] == []


# --- GitHub events for repositories no task uses -----------------------------------------------


class EventSession:
    def __init__(self, connected: bool) -> None:
        self.connected = connected
        self.inserted: list[dict[str, Any]] = []

    async def scalars(self, _statement: Any) -> Any:
        return SimpleNamespace(all=lambda: [SimpleNamespace(organization_id=uuid4(), connection_id=uuid4())])

    async def scalar(self, statement: Any) -> Any:
        if isinstance(statement, Insert):
            values = {column.key: getattr(value, "value", value) for column, value in statement._values.items()}  # type: ignore[union-attr]
            self.inserted.append(values)
            return values["id"]
        return uuid4() if self.connected else None


def _event(event_type: str) -> Any:
    return SimpleNamespace(evidence={"installationId": 1}, delivery_id=str(uuid4()), event_type=event_type,
                           resource_external_id="123")


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type,connected,status,queued", [
    ("workflow_run", False, "unmatched", False),
    ("workflow_run", True, "pending", True),
    ("installation_repositories", False, "pending", True),
])
async def test_only_events_that_can_match_are_queued(monkeypatch: pytest.MonkeyPatch, event_type: str,
                                                     connected: bool, status: str, queued: bool) -> None:
    topics: list[str] = []

    class Outbox:
        def __init__(self, _session: Any) -> None:
            pass

        async def enqueue(self, *, topic: str, **_: Any) -> None:
            topics.append(topic)

    monkeypatch.setattr(event_routing, "TransactionalOutbox", Outbox)
    session = EventSession(connected)
    await event_routing.accept_github_event(session, _event(event_type))
    assert session.inserted[0]["status"] == status
    assert topics == (["integration.event.received"] if queued else [])


# --- Counting every message --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_scheduled_delivery_counts_as_a_message(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[Usage] = []

    async def record(usage: Usage, **_: Any) -> None:
        recorded.append(usage)

    monkeypatch.setattr(usage_records, "record_usage", record)

    @usage_records.metered_endpoint("runtime.sweep")
    async def endpoint(request: Any) -> str:
        return "ok"

    await endpoint(SimpleNamespace(headers={"Upstash-Schedule-Id": "scd_1"}, url="https://x"))
    await endpoint(SimpleNamespace(headers={}, url="https://x"))
    assert [usage.qstash_messages for usage in recorded] == [1, 0]


def test_a_message_outside_a_meter_is_still_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        count_qstash_message("https://api.example/internal/v1/runtime/execute?x=1")
    assert "QStash message outside a meter destination=/internal/v1/runtime/execute" in caplog.text
    with metered() as usage:
        count_qstash_message("https://api.example/internal/v1/runtime/execute")
    assert usage.qstash_messages == 1
