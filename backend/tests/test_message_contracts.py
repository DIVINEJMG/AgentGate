"""The messages sent to the execution service match contracts/dispatch-messages.json (producer side,
architecture plan Phase 4). The execution service checks its side in its own tests. Imports only
the core and the shared code, so the core runs it without the execution service installed."""
from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from audoryn_shared.runtime import outbox_trigger, qstash_trigger, schedule_wake

REPO = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((REPO / "contracts" / "dispatch-messages.json").read_text(encoding="utf-8"))["messages"]
PACKAGES = (REPO / "backend" / "app", REPO / "packages" / "shared" / "audoryn_shared",
            REPO / "services" / "execution" / "audoryn_execution")


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    sent: list[dict[str, object]] = []

    class Provider:
        async def publish(self, **kwargs: object) -> SimpleNamespace:
            sent.append(kwargs)
            return SimpleNamespace(id="message")

    async def nothing(*args: object, **kwargs: object) -> None:
        return None

    for module in (qstash_trigger, schedule_wake):
        monkeypatch.setattr(module.UpstashQStashProvider, "from_settings", classmethod(lambda cls: Provider()))
    monkeypatch.setattr(qstash_trigger.settings, "runtime_execution_enabled", True)
    monkeypatch.setattr(qstash_trigger.settings, "qstash_runtime_execute_url", "https://agentgate.test" + CONTRACT["runtime.execute"]["route"])
    monkeypatch.setattr(qstash_trigger.settings, "execution_call_url", None)
    monkeypatch.setattr(qstash_trigger, "_clear_dispatch_health", nothing)
    monkeypatch.setattr(outbox_trigger, "serving", False)
    return sent


def _matches(name: str, body: dict[str, object]) -> None:
    message = CONTRACT[name]
    assert set(message["required"]) <= set(body) <= set(message["required"]) | set(message["optional"]), body
    for key, value in body.items():
        assert type(value) is type(message["example"][key]), (key, value)


@pytest.mark.asyncio
async def test_a_due_step_is_sent_as_the_contract_says(published: list[dict[str, object]]):
    await qstash_trigger.request_runtime_execution(organization_id=uuid4(), work_item_id=uuid4(), expected_step=2,
                                                   reason="continuation-plan", delay_seconds=5)
    body = json.loads(str(published[0]["body"]))
    _matches("runtime.execute", body)
    assert str(published[0]["destination"]).endswith(CONTRACT["runtime.execute"]["route"])


@pytest.mark.asyncio
async def test_a_schedule_wake_up_is_sent_as_the_contract_says(published: list[dict[str, object]],
                                                               monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(schedule_wake.settings, "qstash_runtime_execute_url",
                        "https://agentgate.test" + CONTRACT["runtime.execute"]["route"])
    monkeypatch.setattr(schedule_wake.settings, "qstash_url", "https://qstash.test")
    monkeypatch.setattr(schedule_wake.settings, "qstash_token", SecretStr("test-token"))
    due = datetime(2026, 10, 22, 9, tzinfo=UTC)
    await schedule_wake.arm_schedule(organization_id=uuid4(), job_id=uuid4(), due=due, now=due)
    assert published, "the wake-up was not sent"
    body = json.loads(str(published[0]["body"]))
    _matches("runtime.schedule_due", body)
    assert str(published[0]["destination"]).endswith(CONTRACT["runtime.schedule_due"]["route"])
    assert datetime.fromisoformat(str(body["due"])) == due and UUID(str(body["job_id"]))


def _enqueued() -> list[tuple[Path, int, str, set[str]]]:
    """Every outbox row queued with a literal topic and payload, in all three packages."""
    found = []
    for root in PACKAGES:
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "enqueue"):
                    continue
                options = {keyword.arg: keyword.value for keyword in node.keywords}
                topic, payload = options.get("topic"), options.get("payload")
                if isinstance(topic, ast.Constant) and isinstance(payload, ast.Dict):
                    keys = {key.value for key in payload.keys if isinstance(key, ast.Constant)}
                    found.append((path, node.lineno, str(topic.value), keys))
    return found


def test_every_queued_message_for_the_execution_service_carries_what_its_route_needs():
    needs = {topic: set(keys) for message in CONTRACT.values() for topic, keys in message.get("topics", {}).items()}
    checked = [(path, line, topic, keys) for path, line, topic, keys in _enqueued() if topic in needs]
    assert {topic for _, _, topic, _ in checked} == set(needs)  # every topic is still produced somewhere
    missing = [f"{path.relative_to(REPO)}:{line} {topic} lacks {sorted(needs[topic] - keys)}"
               for path, line, topic, keys in checked if not needs[topic] <= keys]
    assert not missing, missing
