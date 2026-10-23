"""The execution service accepts exactly the messages in contracts/dispatch-messages.json (consumer
side, architecture plan Phase 4). The senders check theirs in backend/tests/test_message_contracts.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from audoryn_execution.api.internal.integrations import Signal
from audoryn_execution.api.internal.runtime import ExecutePayload, ScheduleDuePayload
from audoryn_execution.api.internal.web_research import ResearchPayload

CONTRACT = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "dispatch-messages.json")
                      .read_text(encoding="utf-8"))["messages"]
MODELS: dict[str, type[BaseModel]] = {
    "runtime.execute": ExecutePayload,
    "runtime.schedule_due": ScheduleDuePayload,
    "integrations.process": Signal,
    "web_research.execute": ResearchPayload,
}


def test_every_message_in_the_contract_has_its_model():
    assert set(MODELS) == set(CONTRACT)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_the_model_accepts_exactly_the_contract(name: str):
    model, message = MODELS[name], CONTRACT[name]
    fields = model.model_fields
    assert {key for key, field in fields.items() if field.is_required()} == set(message["required"])
    assert set(fields) == set(message["required"]) | set(message["optional"])
    model.model_validate(message["example"])
    for topic, keys in message.get("topics", {}).items():  # each queued topic's minimum is accepted
        model.model_validate({key: message["example"].get(key, "00000000-0000-4000-8000-000000000005")
                              for key in keys}), topic
