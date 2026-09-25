from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.ai.providers import (
    AIInvocationContext,
    AIMediaInput,
    AIModelRole,
    AIResponse,
)
from app.runtime.managed import _attach_observation_id
from app.runtime.planner.adaptive import AdaptiveRuntimePlanner


@dataclass
class FakeGateway:
    outputs: list[dict[str, object]]
    calls: int = 0

    async def generate_structured(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, object],
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> dict[str, object]:
        del system, prompt, schema_name, schema, context, max_output_tokens, temperature
        assert role == "planner"
        index = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        return self.outputs[index]

    async def generate_text(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
        stream: bool = False,
    ) -> AIResponse:
        del role, system, prompt, context, max_output_tokens, temperature, stream
        raise AssertionError("planner test must use structured generation")

    async def analyze_media(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        media: AIMediaInput,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
    ) -> AIResponse:
        del role, system, prompt, media, context, max_output_tokens
        raise AssertionError("planner test must not analyze media")


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
    gateway = FakeGateway(
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
    planner = AdaptiveRuntimePlanner(gateway)

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
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_adaptive_planner_rejects_element_action_before_browser_observation() -> None:
    gateway = FakeGateway(
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
    planner = AdaptiveRuntimePlanner(gateway)

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
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_adaptive_planner_uses_only_observed_browser_refs() -> None:
    gateway = FakeGateway(
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
    planner = AdaptiveRuntimePlanner(gateway)
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
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_adaptive_planner_allows_reasoning_only_finish_without_tools() -> None:
    gateway = FakeGateway(
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
    planner = AdaptiveRuntimePlanner(gateway)

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
