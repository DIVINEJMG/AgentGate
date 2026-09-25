from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from app.domain.ai.providers import ModelRequest, ModelResponse
from app.infrastructure.ai.provider import _output_text
from app.runtime.managed import _attach_observation_id
from app.runtime.planner.adaptive import AdaptiveRuntimePlanner


@dataclass
class FakeModel:
    outputs: list[dict[str, object]]
    calls: int = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        assert request.response_format == "json_schema"
        index = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        return ModelResponse(
            text=json.dumps(self.outputs[index]),
            provider="fake",
            model="fake-planner",
        )


def _browser_tools() -> list[dict[str, object]]:
    return [
        {
            "resourceId": "browser-1",
            "resourceName": "QQ browser",
            "provider": "browser",
            "scope": "browser.navigation.open",
            "operation": "navigation.open",
            "description": "Open the authorized site.",
            "risk": "low",
            "inputSchema": {
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string"}},
            },
            "defaultStartUrl": "https://example.com",
        },
        {
            "resourceId": "browser-1",
            "resourceName": "QQ browser",
            "provider": "browser",
            "scope": "browser.element.check",
            "operation": "element.check",
            "description": "Check a checkbox.",
            "risk": "medium",
            "inputSchema": {
                "type": "object",
                "required": ["sessionId", "locator"],
                "properties": {
                    "sessionId": {"type": "string"},
                    "locator": {"type": "object"},
                },
            },
            "defaultStartUrl": "",
        },
    ]


def _job() -> dict[str, object]:
    return {
        "name": "QQ",
        "objective": "Open the page and check the newsletter box.",
        "instructions": "",
        "completionCriteria": ["Newsletter box is checked."],
    }


def _worker() -> dict[str, object]:
    return {"name": "QQ", "responsibilities": [], "instructions": ""}


@pytest.mark.asyncio
async def test_adaptive_planner_treats_capabilities_as_tools_not_checklist() -> None:
    model = FakeModel(
        [
            {
                "decision": "act",
                "summary": "Open the connected site first.",
                "title": "Open site",
                "instruction": "Open the governed browser start URL.",
                "resourceId": "browser-1",
                "scope": "browser.navigation.open",
                "input": {},
            }
        ]
    )
    planner = AdaptiveRuntimePlanner(model)

    decision = await planner.choose_next(
        job=_job(),
        worker=_worker(),
        trigger={},
        tools=_browser_tools(),
        observations=[],
        action_count=0,
        max_actions=8,
    )

    assert decision.scope == "browser.navigation.open"
    assert decision.action_input == {}
    assert model.calls == 1


@pytest.mark.asyncio
async def test_adaptive_planner_rejects_element_action_before_browser_observation() -> None:
    model = FakeModel(
        [
            {
                "decision": "act",
                "summary": "Check the target.",
                "title": "Check target",
                "instruction": "Check the target checkbox.",
                "resourceId": "browser-1",
                "scope": "browser.element.check",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e2"}
                },
            },
            {
                "decision": "act",
                "summary": "Open the site before interacting.",
                "title": "Open site",
                "instruction": "Open the governed start URL.",
                "resourceId": "browser-1",
                "scope": "browser.navigation.open",
                "input": {},
            },
        ]
    )
    planner = AdaptiveRuntimePlanner(model)

    decision = await planner.choose_next(
        job=_job(),
        worker=_worker(),
        trigger={},
        tools=_browser_tools(),
        observations=[],
        action_count=0,
        max_actions=8,
    )

    assert decision.scope == "browser.navigation.open"
    assert model.calls == 2


@pytest.mark.asyncio
async def test_adaptive_planner_uses_only_observed_browser_refs() -> None:
    model = FakeModel(
        [
            {
                "decision": "act",
                "summary": "Check the observed newsletter field.",
                "title": "Check newsletter",
                "instruction": "Check the newsletter checkbox.",
                "resourceId": "browser-1",
                "scope": "browser.element.check",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e99"}
                },
            },
            {
                "decision": "act",
                "summary": "Check the observed newsletter field.",
                "title": "Check newsletter",
                "instruction": "Check the newsletter checkbox.",
                "resourceId": "browser-1",
                "scope": "browser.element.check",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e2"}
                },
            },
        ]
    )
    planner = AdaptiveRuntimePlanner(model)
    observations = [
        {
            "step": 1,
            "scope": "browser.navigation.open",
            "browserObservation": {
                "id": "obs-1",
                "sessionId": "session-1",
                "elements": [
                    {"ref": "e1", "role": "button", "name": "Continue"},
                    {"ref": "e2", "role": "checkbox", "name": "Newsletter"},
                ],
            },
        }
    ]

    decision = await planner.choose_next(
        job=_job(),
        worker=_worker(),
        trigger={},
        tools=_browser_tools(),
        observations=observations,
        action_count=1,
        max_actions=8,
    )

    assert decision.scope == "browser.element.check"
    assert decision.action_input["locator"] == {
        "strategy": "observation_ref",
        "value": "e2",
    }
    assert model.calls == 2



@pytest.mark.asyncio
async def test_adaptive_planner_allows_reasoning_only_finish_without_tools() -> None:
    model = FakeModel(
        [
            {
                "decision": "finish",
                "summary": "The requested internal analysis is complete.",
                "title": "Finish",
                "instruction": "Return the bounded reasoning result.",
                "resourceId": "",
                "scope": "",
                "input": {},
            }
        ]
    )
    planner = AdaptiveRuntimePlanner(model)

    decision = await planner.choose_next(
        job={
            "name": "Internal reasoning",
            "objective": "Summarize the supplied internal context.",
            "instructions": "",
            "completionCriteria": ["Return a concise summary."],
        },
        worker=_worker(),
        trigger={},
        tools=[],
        observations=[],
        action_count=0,
        max_actions=8,
    )

    assert decision.decision == "finish"
    assert decision.scope == ""


def test_runtime_injects_observation_id_into_nested_browser_locators() -> None:
    raw = {
        "fields": [
            {
                "locator": {"strategy": "observation_ref", "value": "e4"},
                "value": "Divine",
            }
        ],
        "submitLocator": {"strategy": "observation_ref", "value": "e8"},
    }

    result = _attach_observation_id(raw, "obs-123")

    assert isinstance(result, dict)
    fields = result["fields"]
    assert isinstance(fields, list)
    assert fields[0]["locator"]["observationId"] == "obs-123"
    assert result["submitLocator"]["observationId"] == "obs-123"


def test_openai_response_text_parser_reads_output_message() -> None:
    payload: dict[str, object] = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "{\"decision\":\"finish\"}"}
                ],
            }
        ]
    }
    assert _output_text(payload) == '{"decision":"finish"}'
