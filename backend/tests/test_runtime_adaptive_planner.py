from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.domain.ai.providers import (
    AIInvocationContext,
    AIMediaInput,
    AIModelRole,
    AIResponse,
)
from app.runtime.managed import _attach_observation_id
from app.runtime.planner.adaptive import AdaptiveRuntimePlanner

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_unknown_tool_pair_is_repaired_from_authorized_catalog() -> None:
    read = {"decision": "act", "summary": "Read the observed page", "title": "Read",
            "instruction": "Read", "resourceId": "browser-1", "scope": "browser.page.read",
            "input": {}}
    gateway = FakeGateway([{**read, "resourceId": "invented-resource"}, read])
    decision = await AdaptiveRuntimePlanner(gateway).choose_next(
        job=_job(), worker=_worker(), trigger={},
        tools=[{"resourceId": "browser-1", "scope": "browser.page.read", "inputSchema": {}}],
        observations=[{"scope": "browser.element.press_key", "browserObservation": {
            "url": "https://example.com", "visibleText": "Results", "elements": []}}],
        action_count=3, max_actions=8,
    )
    assert decision.resource_id == "browser-1"
    assert decision.scope == "browser.page.read"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_planner_does_not_activate_non_search_control_with_enter() -> None:
    enter = {"decision": "act", "summary": "Activate", "title": "Activate",
             "instruction": "Press Enter", "resourceId": "browser-1",
             "scope": "browser.element.press_key",
             "input": {"locator": {"strategy": "observation_ref", "value": "e1"},
                       "value": "Enter"}}
    click = {**enter, "scope": "browser.element.click",
             "input": {"locator": {"strategy": "observation_ref", "value": "e1"}}}
    gateway = FakeGateway([enter, click])
    decision = await AdaptiveRuntimePlanner(gateway).choose_next(
        job=_job(), worker=_worker(), trigger={},
        tools=[{"resourceId": "browser-1", "scope": scope, "inputSchema": {}}
               for scope in ("browser.element.press_key", "browser.element.click")],
        observations=[{"scope": "browser.navigation.open", "browserObservation": {
            "url": "https://example.com", "elements": [{"ref": "e1", "tag": "button",
                "name": "Send", "keyboard_enter_safe": False}]}}],
        action_count=1, max_actions=8,
    )
    assert decision.scope == "browser.element.click"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_planner_repairs_enter_after_inspection_without_replaying_it() -> None:
    enter = {"decision": "act", "summary": "Submit", "title": "Submit",
             "instruction": "Press Enter", "resourceId": "browser-1",
             "scope": "browser.element.press_key",
             "input": {"locator": {"strategy": "observation_ref", "value": "e1"},
                       "value": "Enter"}}
    scroll = {**enter, "scope": "browser.page.scroll", "instruction": "Inspect lower content",
              "input": {"value": 360}}
    page = {"url": "https://example.com", "visibleText": "Search query",
            "elements": [{"ref": "e1", "tag": "input", "value": "query",
                          "keyboard_enter_safe": True}]}
    observations = [
        {"scope": "browser.element.type", "browserObservation": page},
        {"scope": "browser.element.press_key", "actionInput": {"value": "Enter"},
         "browserObservation": {**page, "actionEvidence": {"elementReference": "e1"}}},
        {"scope": "browser.page.read", "browserObservation": page},
    ]
    tools: list[dict[str, object]] = [{"resourceId": "browser-1", "scope": scope, "inputSchema": {}}
             for scope in ("browser.element.press_key", "browser.page.read", "browser.page.scroll")]
    gateway = FakeGateway([enter, scroll])
    decision = await AdaptiveRuntimePlanner(gateway).choose_next(
        job=_job(), worker=_worker(), trigger={}, tools=tools, observations=observations,
        action_count=3, max_actions=8,
    )
    assert decision.scope == "browser.page.scroll"
    assert gateway.calls == 2
    # A stubborn model must not create an endless Enter -> read -> Enter -> read loop.
    with pytest.raises(RuntimeError, match="two-attempt budget"):
        await AdaptiveRuntimePlanner(FakeGateway([enter, enter])).choose_next(
            job=_job(), worker=_worker(), trigger={}, tools=tools, observations=observations,
            action_count=3, max_actions=8,
        )


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
async def test_populated_search_submits_before_following_matching_navigation_link() -> None:
    gateway = FakeGateway([{
        "decision": "act", "summary": "Use the observed search control.",
        "title": "Click Search", "instruction": "Click the search submit control.",
        "resourceId": "browser-1", "scope": "browser.element.click",
        "input": {"locator": {"strategy": "observation_ref", "value": "e15"}},
    }])
    planner = AdaptiveRuntimePlanner(gateway)
    decision = await planner.choose_next(
        job={**_job(), "objective": "Find Real Madrid's latest result on ESPN."},
        worker=_worker(), trigger={},
        tools=[
            {"resourceId": "browser-1", "scope": scope,
             "inputSchema": {"required": ["sessionId", "locator"]}}
            for scope in ("browser.element.click", "browser.navigation.follow_link")
        ],
        observations=[{"scope": "browser.element.type", "browserObservation": {
            "url": "https://africa.espn.com/", "visibleText": "Search",
            "actionEvidence": {"stateChanged": True, "elementReference": "e14"},
            "elements": [
                {"ref": "e14", "role": "combobox", "name": "Search", "value": "Real Madrid"},
                {"ref": "e15", "role": "button", "name": "Search Sports", "element_type": "submit"},
                {"ref": "e52", "role": "link", "name": "&lpos=subnav_team_real_madrid",
                 "href": "https://africa.espn.com/football/team/_/id/86/"},
            ],
        }}],
        action_count=1, max_actions=8,
    )
    assert decision.scope == "browser.element.click"
    assert decision.action_input["locator"] == {
        "strategy": "observation_ref", "value": "e15",
    }
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_search_after_enter_uses_planner_choice_instead_of_matching_link_shortcut() -> None:
    gateway = FakeGateway([{
        "decision": "act", "summary": "Inspect results below the search field.",
        "title": "Scroll for results", "instruction": "Scroll down and inspect the page.",
        "resourceId": "browser-1", "scope": "browser.page.scroll",
        "input": {"value": 480},
    }])
    planner = AdaptiveRuntimePlanner(gateway)
    elements = [
        {"ref": "e14", "role": "combobox", "name": "Search", "value": "Real Madrid"},
        {"ref": "e52", "role": "link", "name": "Real Madrid",
         "href": "https://africa.espn.com/football/team/_/id/86/"},
    ]
    decision = await planner.choose_next(
        job=_job(), worker=_worker(), trigger={},
        tools=[
            {"resourceId": "browser-1", "scope": "browser.navigation.follow_link"},
            {"resourceId": "browser-1", "scope": "browser.page.scroll"},
        ],
        observations=[
            {"scope": "browser.element.type", "browserObservation": {
                "url": "https://africa.espn.com/", "visibleText": "Search menu",
                "elements": elements,
            }},
            {"scope": "browser.element.press_key", "actionInput": {"value": "Enter"},
             "browserObservation": {
                 "url": "https://africa.espn.com/", "visibleText": "Search menu updated",
                 "elements": elements,
                 "actionEvidence": {"elementReference": "e14", "stateChanged": True},
             }},
        ], action_count=2, max_actions=8,
    )
    assert decision.scope == "browser.page.scroll"
    assert decision.action_input["value"] == 480
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_search_result_is_not_reopened_after_an_interleaved_enter() -> None:
    href = "https://africa.espn.com/football/team/_/id/86/"
    elements = [
        {"ref": "e14", "role": "combobox", "name": "Search", "value": "Real Madrid"},
        {"ref": "e52", "role": "link", "name": "Real Madrid", "href": href},
    ]
    gateway = FakeGateway([
        {"decision": "act", "summary": "Open the result again.", "title": "Open result",
         "instruction": "Follow Real Madrid.", "resourceId": "browser-1",
         "scope": "browser.navigation.follow_link",
         "input": {"locator": {"strategy": "observation_ref", "value": "e52"}}},
        {"decision": "act", "summary": "Inspect the current result.", "title": "Read page",
         "instruction": "Read the current page.", "resourceId": "browser-1",
         "scope": "browser.page.read", "input": {}},
    ])
    observations = [
        {"scope": "browser.element.type", "browserObservation": {
            "url": "https://africa.espn.com/", "elements": elements,
            "actionEvidence": {"elementReference": "e14", "stateChanged": True},
        }},
        {"scope": "browser.navigation.follow_link", "browserObservation": {
            "url": href, "elements": elements,
            "actionEvidence": {"elementReference": "e52", "stateChanged": True},
        }},
        {"scope": "browser.element.press_key", "actionInput": {"value": "Enter"},
         "browserObservation": {
             "url": href, "elements": elements,
             "actionEvidence": {"elementReference": "e14", "stateChanged": False},
         }},
    ]

    decision = await AdaptiveRuntimePlanner(gateway).choose_next(
        job={**_job(), "objective": "Find Real Madrid's latest result."},
        worker=_worker(), trigger={},
        tools=[
            {"resourceId": "browser-1", "scope": "browser.navigation.follow_link",
             "inputSchema": {"required": ["sessionId", "locator"]}},
            {"resourceId": "browser-1", "scope": "browser.page.read",
             "inputSchema": {"required": ["sessionId"]}},
        ],
        observations=observations, action_count=3, max_actions=8,
    )

    assert decision.scope == "browser.page.read"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_new_page_evidence_allows_planner_to_choose_enter() -> None:
    elements = [
        {"ref": "e14", "role": "combobox", "name": "Search", "value": "Real Madrid"},
        {"ref": "e52", "role": "link", "name": "Real Madrid",
         "href": "https://africa.espn.com/football/team/_/id/86/"},
    ]
    enter = {"decision": "act", "summary": "Submit search again.", "title": "Submit",
             "instruction": "Press Enter on Search.", "resourceId": "browser-1",
             "scope": "browser.element.press_key",
             "input": {"locator": {"strategy": "observation_ref", "value": "e14"},
                       "value": "Enter"}}
    read = {"decision": "act", "summary": "Inspect the current page.",
            "title": "Read page", "instruction": "Read the current page.",
            "resourceId": "browser-1", "scope": "browser.page.read", "input": {}}
    gateway = FakeGateway([enter, read])
    observations = [
        {"scope": "browser.element.type", "browserObservation": {
            "url": "https://africa.espn.com/", "elements": elements}},
        {"scope": "browser.element.press_key", "actionInput": {"value": "Enter"},
         "browserObservation": {
             "url": "https://africa.espn.com/", "elements": elements,
             "actionEvidence": {"elementReference": "e14", "stateChanged": False}}},
        {"scope": "browser.navigation.follow_link", "browserObservation": {
            "url": "https://africa.espn.com/football/team/_/id/86/",
            "elements": elements,
            "actionEvidence": {"elementReference": "e52", "stateChanged": True}}},
    ]

    decision = await AdaptiveRuntimePlanner(gateway).choose_next(
        job={**_job(), "objective": "Find Real Madrid's latest result."},
        worker=_worker(), trigger={},
        tools=[
            {"resourceId": "browser-1", "scope": "browser.element.press_key",
             "inputSchema": {"required": ["sessionId", "locator", "value"]}},
            {"resourceId": "browser-1", "scope": "browser.page.read",
             "inputSchema": {"required": ["sessionId"]}},
        ],
        observations=observations, action_count=3, max_actions=8,
    )

    assert decision.scope == "browser.element.press_key"
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_populated_search_clicks_unique_button_when_authorized() -> None:
    gateway = FakeGateway([{
        "decision": "act", "summary": "Use the observed submit control.",
        "title": "Click Search", "instruction": "Click the search button.",
        "resourceId": "browser-1", "scope": "browser.element.click",
        "input": {"locator": {"strategy": "observation_ref", "value": "e15"}},
    }])
    planner = AdaptiveRuntimePlanner(gateway)
    decision = await planner.choose_next(
        job=_job(), worker=_worker(), trigger={},
        tools=[{
            "resourceId": "browser-1", "scope": "browser.element.click",
            "inputSchema": {"required": ["sessionId", "locator"]},
        }],
        observations=[{"scope": "browser.element.type", "browserObservation": {
            "url": "https://example.com/", "visibleText": "Search",
            "actionEvidence": {"stateChanged": True, "elementReference": "e14"},
            "elements": [
                {"ref": "e14", "role": "combobox", "name": "Search", "value": "Real Madrid"},
                {"ref": "e15", "role": "button", "name": "Search", "element_type": "submit"},
            ],
        }}], action_count=1, max_actions=8,
    )
    assert decision.scope == "browser.element.click"
    assert isinstance(decision.action_input["locator"], dict)
    assert decision.action_input["locator"]["value"] == "e15"
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_planner_changes_search_action_after_enter_does_not_advance() -> None:
    enter = {
        "decision": "act",
        "summary": "Submit the search.",
        "title": "Press Enter",
        "instruction": "Press Enter on the search field.",
        "resourceId": "browser-1",
        "scope": "browser.element.press_key",
        "input": {"locator": {"strategy": "observation_ref", "value": "e14"}, "value": "Enter"},
    }
    click = {
        "decision": "act",
        "summary": "Use the observed search control.",
        "title": "Click Search",
        "instruction": "Click the search button.",
        "resourceId": "browser-1",
        "scope": "browser.element.click",
        "input": {"locator": {"strategy": "observation_ref", "value": "e15"}},
    }
    gateway = FakeGateway([enter, click])
    elements = [
        {"ref": "e14", "tag": "input", "role": "combobox", "name": "Search", "value": "Real Madrid"},
        {"ref": "e15", "tag": "input", "role": "button", "name": "Search", "element_type": "submit"},
    ]
    observations = [
        {"scope": "browser.element.type", "browserObservation": {
            "url": "https://example.com/", "elements": elements, "visibleText": "Search"
        }},
        {"scope": "browser.element.press_key", "actionInput": enter["input"], "browserObservation": {
            "url": "https://example.com/", "elements": elements, "visibleText": "Search",
            "actionEvidence": {"elementReference": "e14", "stateChanged": True},
        }},
    ]
    planner = AdaptiveRuntimePlanner(gateway)
    decision = await planner.choose_next(
        job=_job(), worker=_worker(), trigger={},
        tools=[*_browser_tools(),
               {"resourceId": "browser-1", "scope": "browser.element.press_key",
                "inputSchema": {"required": ["sessionId", "locator", "value"]}},
               {"resourceId": "browser-1", "scope": "browser.element.click",
                "inputSchema": {"required": ["sessionId", "locator"]}}],
        observations=observations, action_count=2, max_actions=8,
    )
    assert decision.scope == "browser.element.click"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_planner_can_follow_result_after_same_page_search_button() -> None:
    gateway = FakeGateway([{
        "decision": "act",
        "summary": "The search button revealed a Real Madrid result on the same page.",
        "title": "Open Real Madrid result",
        "instruction": "Follow the observed Real Madrid team result.",
        "resourceId": "browser-1",
        "scope": "browser.navigation.follow_link",
        "input": {"locator": {"strategy": "observation_ref", "value": "e30"}},
    }])
    planner = AdaptiveRuntimePlanner(gateway)
    decision = await planner.choose_next(
        job={**_job(), "objective": "Find Real Madrid's latest result."},
        worker=_worker(), trigger={},
        tools=[*_browser_tools(), {
            "resourceId": "browser-1", "scope": "browser.navigation.follow_link",
            "inputSchema": {"required": ["sessionId", "locator"]},
        }],
        observations=[
            {"scope": "browser.element.type", "browserObservation": {
                "url": "https://example.com/", "visibleText": "Search"
            }},
            {"scope": "browser.element.click", "browserObservation": {
                "url": "https://example.com/", "visibleText": "Search\nReal Madrid team",
                "elements": [{"ref": "e30", "tag": "a", "role": "link",
                              "text": "Real Madrid team", "href": "https://example.com/team/madrid"}],
                "actionEvidence": {"elementReference": "e15", "stateChanged": True},
            }},
        ],
        action_count=2, max_actions=8,
    )
    assert decision.scope == "browser.navigation.follow_link"
    assert isinstance(decision.action_input["locator"], dict)
    assert decision.action_input["locator"]["value"] == "e30"
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_planner_follows_observed_search_to_results_without_shortcuts() -> None:
    team_url = "https://africa.espn.com/football/team/_/id/86/"
    results_url = "https://africa.espn.com/football/team/results/_/id/86/"
    gateway = FakeGateway([
        {"decision": "act", "summary": "Use the observed search control.",
         "title": "Click search", "instruction": "Click the observed search button.",
         "resourceId": "browser-1", "scope": "browser.element.click",
         "input": {"locator": {"strategy": "observation_ref", "value": "e15"}}},
        {"decision": "act", "summary": "Inspect the lower part of the search results.",
         "title": "Scroll results", "instruction": "Scroll down to inspect results.",
         "resourceId": "browser-1", "scope": "browser.page.scroll",
         "input": {"value": 500}},
        {"decision": "act", "summary": "The team result is now visible.",
         "title": "Open team", "instruction": "Follow the observed team link.",
         "resourceId": "browser-1", "scope": "browser.navigation.follow_link",
         "input": {"locator": {"strategy": "observation_ref", "value": "e52"}}},
        {"decision": "act", "summary": "The team page exposes Results.",
         "title": "Open results", "instruction": "Follow the observed Results link.",
         "resourceId": "browser-1", "scope": "browser.navigation.follow_link",
         "input": {"locator": {"strategy": "observation_ref", "value": "e70"}}},
        {"decision": "finish", "summary": "The latest dated match and score are visible.",
         "title": "Result found", "instruction": "Report the observed result."},
    ])
    tools: list[dict[str, object]] = [
        {"resourceId": "browser-1", "scope": scope}
        for scope in (
            "browser.element.click", "browser.page.scroll",
            "browser.navigation.follow_link",
        )
    ]
    search_field = {"ref": "e14", "role": "combobox", "name": "Search",
                    "value": "Real Madrid"}
    observations = [{"scope": "browser.element.press_key", "actionInput": {"value": "Enter"},
                     "browserObservation": {
                         "url": "https://africa.espn.com/", "visibleText": "Search",
                         "elements": [search_field,
                                      {"ref": "e15", "role": "button", "name": "Search"}],
                         "actionEvidence": {"result": "executed", "stateChanged": False,
                                            "outcome": "no_observable_change",
                                            "elementReference": "e14"},
                     }}]
    expected_scopes = ["browser.element.click", "browser.page.scroll",
                       "browser.navigation.follow_link", "browser.navigation.follow_link", ""]
    next_observations = [
        {"scope": "browser.element.click", "browserObservation": {
            "url": "https://africa.espn.com/", "visibleText": "Search results",
            "elements": [search_field,
                         {"ref": "e52", "role": "link", "name": "Real Madrid team",
                          "href": team_url}],
            "actionEvidence": {"stateChanged": True, "elementReference": "e15"}}},
        {"scope": "browser.page.scroll", "browserObservation": {
            "url": "https://africa.espn.com/", "visibleText": "Search results Real Madrid team",
            "elements": [{"ref": "e52", "role": "link", "name": "Real Madrid team",
                          "href": team_url}],
            "actionEvidence": {"stateChanged": True}}},
        {"scope": "browser.navigation.follow_link", "browserObservation": {
            "url": team_url, "visibleText": "Real Madrid Overview Results",
            "elements": [{"ref": "e70", "role": "link", "name": "Results",
                          "href": results_url}],
            "actionEvidence": {"stateChanged": True, "elementReference": "e52"}}},
        {"scope": "browser.navigation.follow_link", "browserObservation": {
            "url": results_url, "visibleText": "September 20, 2026 Real Madrid 1 Atlético 2",
            "elements": [],
            "actionEvidence": {"stateChanged": True, "elementReference": "e70"}}},
    ]
    planner = AdaptiveRuntimePlanner(gateway)
    for index, expected_scope in enumerate(expected_scopes):
        decision = await planner.choose_next(
            job={**_job(), "objective": "Find the latest Real Madrid match result.",
                 "completionCriteria": ["Dated match and score observed on ESPN."]},
            worker=_worker(), trigger={}, tools=tools,
            observations=observations, action_count=len(observations), max_actions=8,
        )
        assert decision.scope == expected_scope
        if index < len(next_observations):
            observations.append(next_observations[index])
    assert gateway.calls == 5


@pytest.mark.asyncio
async def test_planner_can_inspect_after_fourth_step_instead_of_auto_following() -> None:
    gateway = FakeGateway([{
        "decision": "act", "summary": "The team page needs inspection below the header.",
        "title": "Read team page", "instruction": "Read the current page.",
        "resourceId": "browser-1", "scope": "browser.page.read", "input": {},
    }])
    decision = await AdaptiveRuntimePlanner(gateway).choose_next(
        job={**_job(), "objective": "Find the latest Real Madrid match result."},
        worker=_worker(), trigger={},
        tools=[{"resourceId": "browser-1", "scope": "browser.page.read"}],
        observations=[
            {"scope": "browser.navigation.open", "browserObservation": {
                "url": "https://africa.espn.com/", "visibleText": "Home"}},
            {"scope": "browser.element.type", "browserObservation": {
                "url": "https://africa.espn.com/", "visibleText": "Search Real Madrid"}},
            {"scope": "browser.element.press_key", "actionInput": {"value": "Enter"},
             "browserObservation": {"url": "https://africa.espn.com/",
                                    "visibleText": "Search Real Madrid"}},
            {"scope": "browser.navigation.follow_link", "browserObservation": {
                "url": "https://africa.espn.com/football/team/_/id/86/",
                "visibleText": "Real Madrid team Overview Results",
                "elements": [{"ref": "e70", "role": "link", "name": "Results"}]}},
        ],
        action_count=4, max_actions=8,
    )
    assert decision.scope == "browser.page.read"
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_planner_handles_non_search_label_and_same_url_content_change() -> None:
    gateway = FakeGateway([
        {"decision": "act", "summary": "Use the observed Go control.",
         "title": "Open results", "instruction": "Click Go.",
         "resourceId": "browser-1", "scope": "browser.element.click",
         "input": {"locator": {"strategy": "observation_ref", "value": "e5"}}},
        {"decision": "act", "summary": "The same page revealed matching content below.",
         "title": "Inspect lower section", "instruction": "Scroll to the new content.",
         "resourceId": "browser-1", "scope": "browser.page.scroll",
         "input": {"value": 360}},
    ])
    planner = AdaptiveRuntimePlanner(gateway)
    observations = [{"scope": "browser.element.type", "browserObservation": {
        "url": "https://example.org/catalog", "visibleText": "Find a product",
        "elements": [{"ref": "e4", "role": "textbox", "name": "Product",
                      "value": "widget"},
                     {"ref": "e5", "role": "button", "name": "Go"}],
        "actionEvidence": {"stateChanged": True, "elementReference": "e4"}}}]
    tools: list[dict[str, object]] = [{"resourceId": "browser-1", "scope": "browser.element.click"},
             {"resourceId": "browser-1", "scope": "browser.page.scroll"}]
    first = await planner.choose_next(job={**_job(), "objective": "Find widget details."},
                                      worker=_worker(), trigger={}, tools=tools,
                                      observations=observations, action_count=1,
                                      max_actions=8)
    observations.append({"scope": "browser.element.click", "browserObservation": {
        "url": "https://example.org/catalog",
        "visibleText": "Find a product\nWidget details available below",
        "elements": [{"ref": "e9", "role": "heading", "name": "Widget details"}],
        "actionEvidence": {"stateChanged": True, "elementReference": "e5"}}})
    second = await planner.choose_next(job={**_job(), "objective": "Find widget details."},
                                       worker=_worker(), trigger={}, tools=tools,
                                       observations=observations, action_count=2,
                                       max_actions=8)
    assert first.scope == "browser.element.click"
    assert second.scope == "browser.page.scroll"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_planner_does_not_repeat_ineffective_search_button_click() -> None:
    repeated = {
        "decision": "act", "summary": "Try Search again.", "title": "Click Search",
        "instruction": "Click the same Search button.", "resourceId": "browser-1",
        "scope": "browser.element.click",
        "input": {"locator": {"strategy": "observation_ref", "value": "e15"}},
    }
    alternative = {
        "decision": "act", "summary": "Inspect search suggestions.", "title": "Read page",
        "instruction": "Read the current page.", "resourceId": "browser-1",
        "scope": "browser.page.read", "input": {},
    }
    gateway = FakeGateway([repeated, alternative])
    search_button = {"ref": "e15", "tag": "input", "role": "button",
                     "element_type": "submit", "name": "Search"}
    decision = await AdaptiveRuntimePlanner(gateway).choose_next(
        job=_job(), worker=_worker(), trigger={},
        tools=[*_browser_tools(),
               {"resourceId": "browser-1", "scope": "browser.element.click",
                "inputSchema": {"required": ["sessionId", "locator"]}},
               {"resourceId": "browser-1", "scope": "browser.page.read",
                "inputSchema": {"required": ["sessionId"]}}],
        observations=[
            {"scope": "browser.element.type", "browserObservation": {
                "url": "https://example.com/", "visibleText": "Search", "elements": [search_button]
            }},
            {"scope": "browser.element.click", "browserObservation": {
                "url": "https://example.com/", "visibleText": "Search", "elements": [search_button],
                "actionEvidence": {"elementReference": "e15", "stateChanged": True},
            }},
        ],
        action_count=2, max_actions=8,
    )
    assert decision.scope == "browser.page.read"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_planner_returns_attention_result_on_site_verification_challenge() -> None:
    gateway = FakeGateway([])
    planner = AdaptiveRuntimePlanner(gateway)

    decision = await planner.choose_next(
        job=_job(),
        worker=_worker(),
        trigger={},
        tools=_browser_tools(),
        observations=[
            {
                "scope": "browser.navigation.open",
                "browserObservation": {
                    "url": "https://www.google.com/sorry/index",
                    "visibleText": "Our systems have detected unusual traffic.",
                },
            }
        ],
        action_count=8,
        max_actions=8,
    )

    assert decision.decision == "finish"
    assert decision.result_status == "attention"
    assert "could not be checked" in decision.summary
    assert gateway.calls == 0


@pytest.mark.asyncio
async def test_planner_refuses_repeated_same_page_navigation_control() -> None:
    repeat = {
        "decision": "act",
        "summary": "Try the leadership control again.",
        "title": "Follow Leadership",
        "instruction": "Click Leadership.",
        "resourceId": "browser-1",
        "scope": "browser.navigation.follow_link",
        "input": {"locator": {"strategy": "observation_ref", "value": "e6"}},
    }
    gateway = FakeGateway(
        [
            repeat,
            {
                "decision": "finish",
                "summary": "The leadership section is present on the observed page.",
                "title": "Finish",
                "instruction": "Use the observed leadership section.",
                "resourceId": "",
                "scope": "",
                "input": {},
            },
        ]
    )
    planner = AdaptiveRuntimePlanner(gateway)
    follow_tool = {
        "resourceId": "browser-1",
        "scope": "browser.navigation.follow_link",
        "inputSchema": {"required": ["sessionId", "locator"]},
    }
    observations = [
        {
            "scope": "browser.navigation.open",
            "browserObservation": {"url": "https://example.com/", "visibleText": "Home"},
        },
        {
            "scope": "browser.navigation.follow_link",
            "browserObservation": {
                "url": "https://example.com/",
                "visibleText": "Leadership section",
                "elements": [{"ref": "e6", "text": "Leadership"}],
                "actionEvidence": {"elementReference": "e6", "stateChanged": False},
            },
        },
    ]

    decision = await planner.choose_next(
        job=_job(),
        worker=_worker(),
        trigger={},
        tools=[*_browser_tools(), follow_tool],
        observations=observations,
        action_count=2,
        max_actions=8,
    )

    assert decision.decision == "finish"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_planner_allows_same_page_control_when_it_reveals_a_new_view() -> None:
    gateway = FakeGateway([{
        "decision": "act",
        "summary": "Advance to another leadership view.",
        "title": "Next view",
        "instruction": "Use the observed next control.",
        "resourceId": "browser-1",
        "scope": "browser.navigation.follow_link",
        "input": {"locator": {"strategy": "observation_ref", "value": "e6"}},
    }])
    planner = AdaptiveRuntimePlanner(gateway)
    decision = await planner.choose_next(
        job={**_job(), "objective": "Find every leader."},
        worker=_worker(),
        trigger={},
        tools=[*_browser_tools(), {
            "resourceId": "browser-1",
            "scope": "browser.navigation.follow_link",
            "inputSchema": {"required": ["sessionId", "locator"]},
        }],
        observations=[
            {"scope": "browser.navigation.open", "browserObservation": {
                "url": "https://example.com/", "visibleText": "First view"
            }},
            {"scope": "browser.navigation.follow_link", "browserObservation": {
                "url": "https://example.com/", "visibleText": "Second view",
                "elements": [{"ref": "e6", "role": "button", "name": "Next view"}],
                "actionEvidence": {"elementReference": "e6", "stateChanged": True},
            }},
        ],
        action_count=2,
        max_actions=20,
    )
    assert decision.scope == "browser.navigation.follow_link"
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_planner_refreshes_same_page_after_repeated_navigation_choice() -> None:
    repeated_click = {
        "decision": "act",
        "summary": "Try Leadership again.",
        "title": "Follow Leadership",
        "instruction": "Click Leadership.",
        "resourceId": "browser-1",
        "scope": "browser.navigation.follow_link",
        "input": {"locator": {"strategy": "observation_ref", "value": "e6"}},
    }
    planner = AdaptiveRuntimePlanner(FakeGateway([repeated_click]))

    decision = await planner.choose_next(
        job=_job(),
        worker=_worker(),
        trigger={},
        tools=[
            *_browser_tools(),
            {
                "resourceId": "browser-1",
                "scope": "browser.navigation.follow_link",
                "inputSchema": {"required": ["sessionId", "locator"]},
            },
            {
                "resourceId": "browser-1",
                "scope": "browser.page.read",
                "inputSchema": {"required": ["sessionId"]},
            },
        ],
        observations=[
            {
                "scope": "browser.navigation.open",
                "browserObservation": {"url": "https://example.com/"},
            },
            {
                "scope": "browser.navigation.follow_link",
                "browserObservation": {
                    "url": "https://example.com/",
                    "elements": [{"ref": "e6", "text": "Leadership"}],
                    "actionEvidence": {"elementReference": "e6", "stateChanged": False},
                },
            },
        ],
        action_count=2,
        max_actions=8,
    )

    assert decision.decision == "act"
    assert decision.scope == "browser.page.read"
    assert decision.action_input == {}


@pytest.mark.asyncio
async def test_planner_reads_later_same_page_sections_before_finishing() -> None:
    class InspectingGateway(FakeGateway):
        async def generate_structured(self, **kwargs):  # type: ignore[override]
            prompt = str(kwargs["prompt"])
            assert "LEADERSHIP\nRotimi Ibrahim" in prompt
            assert "Next leadership slide" in prompt
            return await super().generate_structured(**kwargs)

    gateway = InspectingGateway([
        {
            "decision": "finish",
            "summary": "The leadership section lists Rotimi Ibrahim as Group Managing Director.",
            "title": "Leadership found",
            "instruction": "Report the observed leader and role.",
            "resourceId": "",
            "scope": "",
            "input": {},
        }
    ])
    planner = AdaptiveRuntimePlanner(gateway)
    decision = await planner.choose_next(
        job={**_job(), "objective": "Find the company leadership."},
        worker=_worker(),
        trigger={},
        tools=_browser_tools(),
        observations=[{
            "scope": "browser.page.read",
            "browserObservation": {
                "url": "https://example.com/",
                "visibleText": "A" * 4000 + "\nLEADERSHIP\nRotimi Ibrahim - Group Managing Director",
                "elements": [],
                "pageState": {"focusedSection": {
                    "heading": "LEADERSHIP",
                    "text": "Rotimi Ibrahim - Group Managing Director",
                    "controls": [{"role": "button", "name": "Next leadership slide"}],
                }},
            },
        }],
        action_count=2,
        max_actions=8,
    )
    assert decision.decision == "finish"


@pytest.mark.asyncio
async def test_planner_retains_new_content_from_earlier_carousel_views() -> None:
    class InspectingGateway(FakeGateway):
        async def generate_structured(self, **kwargs):  # type: ignore[override]
            prompt = str(kwargs["prompt"])
            assert "Rotimi Ibrahim" in prompt
            assert "Sunday Aikulola" in prompt
            return await super().generate_structured(**kwargs)

    gateway = InspectingGateway([{
        "decision": "finish", "summary": "The observed carousel covered both views.",
        "title": "Leadership found", "instruction": "Report the observed people.",
        "resourceId": "", "scope": "", "input": {},
    }])
    planner = AdaptiveRuntimePlanner(gateway)
    observations = [{
        "step": index,
        "scope": "browser.page.read",
        "browserObservation": {
            "url": "https://example.com/",
            "visibleText": text,
        },
    } for index, text in enumerate(
        ["Leadership\nRotimi Ibrahim"] + ["Leadership"] * 6
        + ["Leadership\nSunday Aikulola"], start=1
    )]
    decision = await planner.choose_next(
        job={**_job(), "objective": "Find the leadership team."},
        worker=_worker(), trigger={}, tools=_browser_tools(),
        observations=observations, action_count=8, max_actions=20,
    )
    assert decision.decision == "finish"


@pytest.mark.asyncio
async def test_planner_can_click_a_card_using_its_observed_name() -> None:
    gateway = FakeGateway([{
        "decision": "act",
        "summary": "Open the observed leadership profile.",
        "title": "Read profile",
        "instruction": "Open the profile for Sunday Aikulola.",
        "resourceId": "browser-1",
        "scope": "browser.element.click",
        "input": {"locator": {"strategy": "text", "value": "Sunday Aikulola", "exact": True}},
    }])
    planner = AdaptiveRuntimePlanner(gateway)
    decision = await planner.choose_next(
        job={**_job(), "objective": "Read a leadership profile."},
        worker=_worker(),
        trigger={},
        tools=[*_browser_tools(), {
            "resourceId": "browser-1",
            "scope": "browser.element.click",
            "inputSchema": {"required": ["sessionId", "locator"]},
        }],
        observations=[{
            "scope": "browser.page.read",
            "browserObservation": {
                "url": "https://example.com/",
                "visibleText": "Leadership\nSunday Aikulola\nRead profile",
                "elements": [],
            },
        }],
        action_count=2,
        max_actions=8,
    )
    assert decision.scope == "browser.element.click"
    assert decision.action_input["locator"] == {
        "strategy": "text", "value": "Sunday Aikulola", "exact": True
    }


@pytest.mark.asyncio
async def test_planner_replaces_ambiguous_profile_label_with_unique_card_name() -> None:
    gateway = FakeGateway([
        {
            "decision": "act",
            "summary": "Open Rotimi's profile.",
            "title": "Read profile",
            "instruction": "Click Read Profile for Rotimi Ibrahim.",
            "resourceId": "browser-1",
            "scope": "browser.navigation.follow_link",
            "input": {"locator": {"strategy": "text", "value": "READ PROFILE"}},
        },
        {
            "decision": "act",
            "summary": "Open the uniquely named card.",
            "title": "Read profile",
            "instruction": "Click Rotimi Ibrahim's card.",
            "resourceId": "browser-1",
            "scope": "browser.navigation.follow_link",
            "input": {"locator": {"strategy": "text", "value": "Rotimi Ibrahim"}},
        },
    ])
    planner = AdaptiveRuntimePlanner(gateway)
    tools: list[dict[str, object]] = [*_browser_tools(), {
        "resourceId": "browser-1",
        "scope": "browser.navigation.follow_link",
        "inputSchema": {"required": ["sessionId", "locator"]},
    }]
    decision = await planner.choose_next(
        job={**_job(), "objective": "Read Rotimi Ibrahim's profile."},
        worker=_worker(), trigger={}, tools=tools,
        observations=[{
            "scope": "browser.page.read",
            "browserObservation": {
                "url": "https://example.com/",
                "visibleText": (
                    "Rotimi Ibrahim\nREAD PROFILE\nMary Ibrahim\nREAD PROFILE"
                ),
                "elements": [],
            },
        }],
        action_count=2, max_actions=20,
    )
    assert gateway.calls == 2
    assert decision.scope == "browser.navigation.follow_link"
    assert isinstance(decision.action_input["locator"], dict)
    assert decision.action_input["locator"]["value"] == "Rotimi Ibrahim"


@pytest.mark.asyncio
async def test_planner_can_advance_a_carousel_control_found_in_focused_section() -> None:
    gateway = FakeGateway([{
        "decision": "act",
        "summary": "There are more leadership entries to inspect.",
        "title": "Next leadership view",
        "instruction": "Use the observed next slide control.",
        "resourceId": "browser-1",
        "scope": "browser.navigation.follow_link",
        "input": {"locator": {
            "strategy": "role", "value": "button", "name": "Next leadership slide"
        }},
    }])
    planner = AdaptiveRuntimePlanner(gateway)
    decision = await planner.choose_next(
        job={**_job(), "objective": "Find the leadership team."},
        worker=_worker(), trigger={},
        tools=[*_browser_tools(), {
            "resourceId": "browser-1",
            "scope": "browser.navigation.follow_link",
            "inputSchema": {"required": ["sessionId", "locator"]},
        }],
        observations=[{
            "scope": "browser.page.read",
            "browserObservation": {
                "url": "https://example.com/",
                "visibleText": "Leadership\nFirst view",
                "elements": [],
                "pageState": {"focusedSection": {
                    "heading": "Leadership",
                    "controls": [{"role": "button", "name": "Next leadership slide"}],
                }},
            },
        }],
        action_count=2, max_actions=20,
    )
    assert decision.scope == "browser.navigation.follow_link"


@pytest.mark.asyncio
async def test_planner_rejects_unobserved_card_text() -> None:
    gateway = FakeGateway([{
        "decision": "act",
        "summary": "Open an unobserved profile.",
        "title": "Read profile",
        "instruction": "Open the profile.",
        "resourceId": "browser-1",
        "scope": "browser.element.click",
        "input": {"locator": {"strategy": "text", "value": "Invented Person"}},
    }])
    planner = AdaptiveRuntimePlanner(gateway)
    with pytest.raises(RuntimeError, match="latest observed page text"):
        await planner.choose_next(
            job=_job(),
            worker=_worker(),
            trigger={},
            tools=[*_browser_tools(), {
                "resourceId": "browser-1",
                "scope": "browser.element.click",
                "inputSchema": {"required": ["sessionId", "locator"]},
            }],
            observations=[{
                "scope": "browser.page.read",
                "browserObservation": {
                    "url": "https://example.com/",
                    "visibleText": "Leadership\nSunday Aikulola",
                    "elements": [],
                },
            }],
            action_count=2,
            max_actions=8,
        )


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


def test_browser_scroll_contract_accepts_semantic_boundaries() -> None:
    browser_provider = (
        ROOT / "backend/app/execution/providers/browser.py"
    ).read_text(encoding="utf-8")
    browser_runtime = (
        ROOT / "backend/app/execution/browser/runtime.py"
    ).read_text(encoding="utf-8")

    assert '"enum": ["top", "bottom"]' in browser_provider
    assert "Browser page.scroll value must be an integer, top, or bottom." in browser_runtime


def test_browser_execution_preserves_current_observation_for_locator_actions() -> None:
    runtime = (
        ROOT / "backend/app/execution/browser/runtime.py"
    ).read_text(encoding="utf-8")
    provider = (
        ROOT / "backend/app/execution/providers/browser.py"
    ).read_text(encoding="utf-8")

    assert "async def current_observation(" in runtime
    assert "if handle.last_observation is not None:" in runtime
    assert "before_observation = await self._runtime.current_observation(" in provider


@pytest.mark.asyncio
async def test_adaptive_planner_rejects_repeat_scroll_without_new_actionable_state() -> None:
    scroll_tool = {
        "resourceId": "browser-1",
        "resourceName": "QQ browser",
        "provider": "browser",
        "scope": "browser.page.scroll",
        "operation": "page.scroll",
        "description": "Scroll the page.",
        "risk": "low",
        "inputSchema": {
            "type": "object",
            "required": ["sessionId", "value"],
            "properties": {
                "sessionId": {"type": "string"},
                "value": {"type": "integer"},
            },
        },
        "defaultStartUrl": "",
    }
    gateway = FakeGateway(
        [
            {
                "decision": "act",
                "summary": "Scroll again.",
                "title": "Scroll again",
                "instruction": "Keep scrolling.",
                "resourceId": "browser-1",
                "scope": "browser.page.scroll",
                "input": {"value": 900},
            },
            {
                "decision": "act",
                "summary": "Use the already observed checkbox.",
                "title": "Check newsletter",
                "instruction": "Check the observed checkbox.",
                "resourceId": "browser-1",
                "scope": "browser.element.check",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e2"}
                },
            },
        ]
    )
    planner = AdaptiveRuntimePlanner(gateway)
    browser_observation = {
        "url": "https://example.com",
        "formDetails": [{"ref": "f1", "fieldRefs": ["e2"], "submitRefs": []}],
        "elements": [
            {"ref": "e2", "role": "checkbox", "name": "Newsletter"}
        ],
        "pageState": {"formCount": 1, "interactiveElementCount": 1},
    }
    observations = [
        {
            "step": 1,
            "scope": "browser.navigation.open",
            "browserObservation": dict(browser_observation),
        },
        {
            "step": 2,
            "scope": "browser.page.scroll",
            "browserObservation": dict(browser_observation),
        },
    ]

    decision = await planner.choose_next(
        job=_job(),
        worker=_worker(),
        trigger={},
        tools=[*_browser_tools(), scroll_tool],
        observations=observations,
        action_count=2,
        max_actions=8,
    )

    assert decision.scope == "browser.element.check"
    assert gateway.calls == 2


def test_planner_observation_prioritizes_form_refs_before_page_prose() -> None:
    source = (ROOT / "backend/app/runtime/managed.py").read_text(encoding="utf-8")
    browser_block_start = source.index('entry["browserObservation"] = {')
    browser_block = source[browser_block_start : browser_block_start + 1800]

    assert browser_block.index('"formDetails"') < browser_block.index('"visibleText"')
    assert browser_block.index('"elements"') < browser_block.index('"visibleText"')
    assert '"value", 240' in source
    assert '"field_name"' in source
    assert '"elementReference": action_evidence.get("elementReference")' in source
    assert "form_refs" in source



@pytest.mark.asyncio
async def test_adaptive_planner_rejects_typing_value_already_present() -> None:
    type_tool = {
        "resourceId": "browser-1",
        "resourceName": "Search browser",
        "provider": "browser",
        "scope": "browser.element.type",
        "operation": "element.type",
        "description": "Type into a field.",
        "risk": "medium",
        "inputSchema": {
            "type": "object",
            "required": ["sessionId", "locator", "value"],
            "properties": {
                "sessionId": {"type": "string"},
                "locator": {"type": "object"},
                "value": {},
            },
        },
        "defaultStartUrl": "",
    }
    gateway = FakeGateway(
        [
            {
                "decision": "act",
                "summary": "Type the query again.",
                "title": "Enter search query",
                "instruction": "Type the search query into the observed field.",
                "resourceId": "browser-1",
                "scope": "browser.element.type",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e11"},
                    "value": "latest Real Madrid result",
                },
            },
            {
                "decision": "act",
                "summary": "Continue using the authorized search URL.",
                "title": "Submit search",
                "instruction": "Open the same-origin GET search URL.",
                "resourceId": "browser-1",
                "scope": "browser.navigation.open",
                "input": {
                    "url": "https://example.com/search?q=latest+Real+Madrid+result"
                },
            },
        ]
    )
    planner = AdaptiveRuntimePlanner(gateway)
    observations = [
        {
            "step": 2,
            "scope": "browser.element.type",
            "browserObservation": {
                "url": "https://example.com/",
                "elements": [
                    {
                        "ref": "e11",
                        "tag": "textarea",
                        "role": "combobox",
                        "name": "Search",
                        "value": "latest Real Madrid result",
                    }
                ],
                "formDetails": [
                    {"ref": "f1", "fieldRefs": ["e11"], "submitRefs": ["e16"]}
                ],
            },
        }
    ]

    decision = await planner.choose_next(
        job={
            "name": "Search",
            "objective": "Search for the latest result.",
            "instructions": "",
            "completionCriteria": ["Search results are visible."],
        },
        worker=_worker(),
        trigger={},
        tools=[*_browser_tools(), type_tool],
        observations=observations,
        action_count=2,
        max_actions=8,
    )

    assert decision.scope == "browser.navigation.open"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_adaptive_planner_rejects_consecutive_retype_of_populated_field() -> None:
    type_tool = {
        "resourceId": "browser-1",
        "resourceName": "Search browser",
        "provider": "browser",
        "scope": "browser.element.type",
        "operation": "element.type",
        "description": "Type into a field.",
        "risk": "medium",
        "inputSchema": {
            "type": "object",
            "required": ["sessionId", "locator", "value"],
            "properties": {
                "sessionId": {"type": "string"},
                "locator": {"type": "object"},
                "value": {},
            },
        },
        "defaultStartUrl": "",
    }
    gateway = FakeGateway(
        [
            {
                "decision": "act",
                "summary": "Refine the query.",
                "title": "Enter search query",
                "instruction": "Replace the query with a slightly different phrase.",
                "resourceId": "browser-1",
                "scope": "browser.element.type",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e12"},
                    "value": "latest Real Madrid match result",
                },
            },
            {
                "decision": "act",
                "summary": "Advance to the search results.",
                "title": "Submit search",
                "instruction": "Open the authorized GET search URL.",
                "resourceId": "browser-1",
                "scope": "browser.navigation.open",
                "input": {
                    "url": "https://example.com/search?q=latest+Real+Madrid+result"
                },
            },
        ]
    )
    planner = AdaptiveRuntimePlanner(gateway)
    observations = [
        {
            "step": 2,
            "scope": "browser.element.type",
            "browserObservation": {
                "url": "https://example.com/",
                "elements": [
                    {
                        "ref": "e12",
                        "tag": "textarea",
                        "role": "combobox",
                        "name": "Search",
                        "value": "latest Real Madrid result",
                    }
                ],
                "formDetails": [
                    {"ref": "f1", "fieldRefs": ["e12"], "submitRefs": ["e16"]}
                ],
                "actionEvidence": {
                    "operation": "element.type",
                    "stateChanged": True,
                    "elementReference": "e12",
                },
            },
        }
    ]

    decision = await planner.choose_next(
        job={
            "name": "Search",
            "objective": "Search for the latest result.",
            "instructions": "",
            "completionCriteria": ["Search results are visible."],
        },
        worker=_worker(),
        trigger={},
        tools=[*_browser_tools(), type_tool],
        observations=observations,
        action_count=2,
        max_actions=8,
    )

    assert decision.scope == "browser.navigation.open"
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_adaptive_planner_replans_text_sent_as_a_key_to_search_button() -> None:
    gateway = FakeGateway(
        [
            {
                "decision": "act",
                "summary": "Search ESPN for Real Madrid.",
                "title": "Search ESPN",
                "instruction": "Open search and type Real Madrid.",
                "resourceId": "browser-1",
                "scope": "browser.element.press_key",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e13"},
                    "value": "Real Madrid",
                },
            },
            {
                "decision": "act",
                "summary": "Open ESPN search.",
                "title": "Open search",
                "instruction": "Click the observed Open Search button.",
                "resourceId": "browser-1",
                "scope": "browser.element.click",
                "input": {"locator": {"strategy": "observation_ref", "value": "e13"}},
            },
        ]
    )
    planner = AdaptiveRuntimePlanner(gateway)
    tools: list[dict[str, object]] = [
        {
            "resourceId": "browser-1",
            "scope": scope,
            "inputSchema": {"required": ["sessionId", "locator", *extra]},
        }
        for scope, extra in (
            ("browser.element.press_key", ["value"]),
            ("browser.element.click", []),
        )
    ]

    decision = await planner.choose_next(
        job={**_job(), "objective": "Search ESPN for Real Madrid."},
        worker=_worker(),
        trigger={},
        tools=tools,
        observations=[
            {
                "scope": "browser.navigation.open",
                "browserObservation": {
                    "url": "https://africa.espn.com/",
                    "elements": [
                        {"ref": "e13", "role": "button", "name": "Open Search"},
                        {"ref": "e14", "role": "combobox", "name": "Search"},
                    ],
                },
            }
        ],
        action_count=1,
        max_actions=8,
    )

    assert gateway.calls == 2
    assert decision.scope == "browser.element.click"
    assert isinstance(decision.action_input["locator"], dict)
    assert decision.action_input["locator"]["value"] == "e13"


@pytest.mark.asyncio
async def test_adaptive_planner_redirects_enter_form_shortcut_to_form_submit() -> None:
    press_tool = {
        "resourceId": "browser-1",
        "resourceName": "Search browser",
        "provider": "browser",
        "scope": "browser.element.press_key",
        "operation": "element.press_key",
        "description": "Press a key outside form submission.",
        "risk": "medium",
        "inputSchema": {
            "type": "object",
            "required": ["sessionId", "locator", "value"],
            "properties": {
                "sessionId": {"type": "string"},
                "locator": {"type": "object"},
                "value": {"type": "string"},
            },
        },
        "defaultStartUrl": "",
    }
    submit_tool = {
        "resourceId": "browser-1",
        "resourceName": "Search browser",
        "provider": "browser",
        "scope": "browser.form.submit",
        "operation": "form.submit",
        "description": "Submit a governed form.",
        "risk": "high",
        "inputSchema": {
            "type": "object",
            "required": ["sessionId"],
            "properties": {
                "sessionId": {"type": "string"},
                "formRef": {"type": "string"},
            },
        },
        "defaultStartUrl": "",
    }
    gateway = FakeGateway(
        [
            {
                "decision": "act",
                "summary": "Press Enter to submit.",
                "title": "Submit search",
                "instruction": "Press Enter in the search field.",
                "resourceId": "browser-1",
                "scope": "browser.element.press_key",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e11"},
                    "value": "Enter",
                },
            },
            {
                "decision": "act",
                "summary": "Submit the observed search form.",
                "title": "Submit search",
                "instruction": "Submit the observed search form.",
                "resourceId": "browser-1",
                "scope": "browser.form.submit",
                "input": {"formRef": "f1"},
            },
        ]
    )
    planner = AdaptiveRuntimePlanner(gateway)
    observations = [
        {
            "step": 2,
            "scope": "browser.element.type",
            "browserObservation": {
                "url": "https://example.com/",
                "elements": [
                    {
                        "ref": "e11",
                        "tag": "textarea",
                        "role": "combobox",
                        "value": "latest result",
                    }
                ],
                "formDetails": [
                    {"ref": "f1", "fieldRefs": ["e11"], "submitRefs": ["e16"]}
                ],
            },
        }
    ]

    decision = await planner.choose_next(
        job=_job(),
        worker=_worker(),
        trigger={},
        tools=[*_browser_tools(), press_tool, submit_tool],
        observations=observations,
        action_count=2,
        max_actions=8,
    )

    assert decision.scope == "browser.form.submit"
    assert decision.action_input == {"formRef": "f1"}
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_adaptive_planner_allows_observed_read_only_search_enter() -> None:
    press_tool = {
        "resourceId": "browser-1",
        "resourceName": "Search browser",
        "provider": "browser",
        "scope": "browser.element.press_key",
        "operation": "element.press_key",
        "description": "Press a key outside form submission.",
        "risk": "medium",
        "inputSchema": {
            "type": "object",
            "required": ["sessionId", "locator", "value"],
            "properties": {
                "sessionId": {"type": "string"},
                "locator": {"type": "object"},
                "value": {"type": "string"},
            },
        },
        "defaultStartUrl": "",
    }
    open_tool = _browser_tools()[0]
    gateway = FakeGateway(
        [
            {
                "decision": "act",
                "summary": "Submit the search.",
                "title": "Submit search",
                "instruction": "Press Enter in the populated search field.",
                "resourceId": "browser-1",
                "scope": "browser.element.press_key",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e12"},
                    "value": "Enter",
                },
            }
        ]
    )
    planner = AdaptiveRuntimePlanner(gateway)
    observations = [
        {
            "step": 2,
            "scope": "browser.element.type",
            "browserObservation": {
                "url": "https://example.com/",
                "elements": [
                    {
                        "ref": "e12",
                        "tag": "textarea",
                        "role": "combobox",
                        "name": "Search", "keyboard_enter_safe": True,
                        "field_name": "q",
                        "value": "latest Real Madrid result",
                    }
                ],
                "formDetails": [
                    {
                        "ref": "f1",
                        "action": "https://example.com/search",
                        "method": "get",
                        "fieldRefs": ["e12"],
                        "submitRefs": ["e16"],
                    }
                ],
            },
        }
    ]

    decision = await planner.choose_next(
        job={
            "name": "Search", "keyboard_enter_safe": True,
            "objective": "Search for the latest result.",
            "instructions": "",
            "completionCriteria": ["Search results are visible."],
        },
        worker=_worker(),
        trigger={},
        tools=[open_tool, press_tool],
        observations=observations,
        action_count=2,
        max_actions=8,
    )

    assert decision.scope == "browser.element.press_key"
    assert decision.resource_id == "browser-1"
    assert decision.action_input["value"] == "Enter"
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_adaptive_planner_rejects_enter_form_shortcut_when_submit_not_authorized() -> None:
    press_tool = {
        "resourceId": "browser-1",
        "resourceName": "Search browser",
        "provider": "browser",
        "scope": "browser.element.press_key",
        "operation": "element.press_key",
        "description": "Press a key outside form submission.",
        "risk": "medium",
        "inputSchema": {
            "type": "object",
            "required": ["sessionId", "locator", "value"],
            "properties": {
                "sessionId": {"type": "string"},
                "locator": {"type": "object"},
                "value": {"type": "string"},
            },
        },
        "defaultStartUrl": "",
    }
    gateway = FakeGateway(
        [
            {
                "decision": "act",
                "summary": "Press Enter to submit.",
                "title": "Submit search",
                "instruction": "Press Enter in the search field.",
                "resourceId": "browser-1",
                "scope": "browser.element.press_key",
                "input": {
                    "locator": {"strategy": "observation_ref", "value": "e11"},
                    "value": "Enter",
                },
            },
            {
                "decision": "act",
                "summary": "Use the authorized GET search route.",
                "title": "Submit search",
                "instruction": "Navigate to the same-origin search URL.",
                "resourceId": "browser-1",
                "scope": "browser.navigation.open",
                "input": {"url": "https://example.com/search?q=latest+result"},
            },
        ]
    )
    planner = AdaptiveRuntimePlanner(gateway)
    observations = [
        {
            "step": 2,
            "scope": "browser.element.type",
            "browserObservation": {
                "url": "https://example.com/",
                "elements": [
                    {
                        "ref": "e11",
                        "tag": "textarea",
                        "role": "combobox",
                        "value": "latest result",
                    }
                ],
                "formDetails": [
                    {"ref": "f1", "fieldRefs": ["e11"], "submitRefs": ["e16"]}
                ],
            },
        }
    ]

    decision = await planner.choose_next(
        job=_job(),
        worker=_worker(),
        trigger={},
        tools=[*_browser_tools(), press_tool],
        observations=observations,
        action_count=2,
        max_actions=8,
    )

    assert decision.scope == "browser.navigation.open"
    assert gateway.calls == 2
