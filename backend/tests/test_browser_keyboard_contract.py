from __future__ import annotations

import json
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from app.execution.browser.contracts import BrowserLocator
from app.execution.browser.keyboard import read_only_key, read_only_search_enter
from app.execution.browser.runtime import BrowserRuntime
from app.execution.providers.browser import CAPABILITIES
from app.runtime.planner.adaptive import AdaptiveRuntimePlanner, _repeated_ineffective_enter


def test_schema_rejects_invented_and_cross_resource_pairs() -> None:
    tools: list[dict[str, object]] = [{"resourceId": "one", "scope": "browser.page.read"},
             {"resourceId": "two", "scope": "browser.navigation.open"}]
    validator = Draft202012Validator(AdaptiveRuntimePlanner._schema(tools))
    decision = {"decision": "act", "resultStatus": "completed", "summary": "Inspect", "title": "Read",
                "instruction": "Read", "resourceId": "one",
                "scope": "browser.page.read", "input": {}}
    assert validator.is_valid(decision)
    assert not validator.is_valid({**decision, "scope": "browser.navigation.open"})
    assert not validator.is_valid({**decision, "resourceId": "invented"})
    assert validator.is_valid({**decision, "decision": "finish", "resourceId": "", "scope": ""})
    assert not Draft202012Validator(AdaptiveRuntimePlanner._schema([])).is_valid(decision)


def test_prompt_preserves_large_complete_tool_catalog() -> None:
    tools = [{"resourceId": f"browser-{index}", "scope": capability.scope,
              "inputSchema": capability.input_schema, "description": capability.description}
             for index in range(3) for capability in CAPABILITIES]
    prompt = AdaptiveRuntimePlanner(None)._prompt(  # type: ignore[arg-type]
        job={}, worker={}, trigger={}, tools=tools, observations=[], latest_browser=None,
        action_count=0, max_actions=20, validation_feedback="",
    )
    serialized = prompt.split("AUTHORIZED TOOLS\n", 1)[1].split("\n\nRECORDED OBSERVATIONS", 1)[0]
    assert len(serialized) > 12000
    assert json.loads(serialized) == tools


def test_same_url_text_and_state_changes_are_enter_progress() -> None:
    before = {"url": "https://example.com", "visibleText": "Old content",
              "elements": [{"ref": "e1", "tag": "input", "value": "query"}]}
    after = {**before, "visibleText": "New result", "actionEvidence": {
        "elementReference": "e1", "stateChanged": True}}
    observations = [{"scope": "browser.element.type", "browserObservation": before},
                    {"scope": "browser.element.press_key", "actionInput": {"value": "Enter"},
                     "browserObservation": after}]
    assert not _repeated_ineffective_enter(observations, "e1")
    after["visibleText"] = "Old content"
    # A generic DOM fingerprint change (for example an advert) is not sufficient
    # to replay Enter when text, URL and actionable controls remain unchanged.
    assert _repeated_ineffective_enter(observations, "e1")
    after["actionEvidence"]["stateChanged"] = False
    assert _repeated_ineffective_enter(observations, "e1")


def test_enter_guard_survives_read_and_scroll() -> None:
    page = {"url": "https://example.com", "visibleText": "Search query",
            "elements": [{"ref": "e1", "tag": "input", "value": "query"}]}
    pressed = {**page, "actionEvidence": {"elementReference": "e1", "stateChanged": False}}
    observations = [
        {"scope": "browser.element.type", "browserObservation": page},
        {"scope": "browser.element.press_key", "actionInput": {"value": "Enter"},
         "browserObservation": pressed},
        {"scope": "browser.page.read", "browserObservation": page},
        {"scope": "browser.page.scroll", "browserObservation": {**page, "visibleText": "Footer"}},
    ]
    assert _repeated_ineffective_enter(observations, "e1")


def test_enter_attempt_budget_ignores_unrelated_page_updates() -> None:
    page = {"url": "https://example.com", "visibleText": "Search query",
            "elements": [{"ref": "e1", "tag": "input", "value": "query"}]}
    observations = [{"scope": "browser.element.type", "browserObservation": page}]
    for number in range(2):
        observations.append({
            "scope": "browser.element.press_key", "actionInput": {"value": "Enter"},
            "browserObservation": {**page, "visibleText": f"Advert {number}",
                "actionEvidence": {"elementReference": "e1", "stateChanged": True}},
        })
        if number == 0:
            assert not _repeated_ineffective_enter(observations, "e1")
        observations.append({"scope": "browser.page.read", "browserObservation": page})
    assert _repeated_ineffective_enter(observations, "e1")
    # A new query or a new page is independently actionable, even on the same ref.
    observations.append({"scope": "browser.element.type", "browserObservation": {
        **page, "elements": [{"ref": "e1", "tag": "input", "value": "another query"}]}})
    assert not _repeated_ineffective_enter(observations, "e1")
    observations.append({"scope": "browser.navigation.open", "browserObservation": {
        **page, "url": "https://example.com/other"}})
    assert not _repeated_ineffective_enter(observations, "e1")


@pytest.mark.parametrize("changes", [
    {"formMethod": "post"}, {"type": "password"},
    {"formFields": [{"type": "hidden", "name": "token"}]},
    {"formFields": [{"type": "password"}]}, {"label": "Send message"},
])
def test_keyboard_does_not_grant_write_or_secret_submission(changes: dict[str, object]) -> None:
    assert not read_only_search_enter({"tag": "input", "label": "Search",
                                       "formMethod": "get", **changes})


def test_read_only_keyboard_metadata_and_default_authority() -> None:
    key = next(cap for cap in CAPABILITIES if cap.scope == "browser.element.press_key")
    assert key.approval_recommendation == "none"
    assert not key.side_effect
    assert read_only_key({"tag": "input", "label": "Search"}, "Enter")
    assert read_only_key({"tag": "button"}, "Tab")
    assert not read_only_key({"tag": "button", "label": "Buy"}, "Enter")
    assert not read_only_key({"tag": "input"}, "Control+Enter")


@pytest.fixture
async def browser_session():
    runtime = BrowserRuntime(headless=True)
    organization = uuid4()
    session = await runtime.create_session(organization_id=organization, worker_id=None,
                                           run_id=uuid4())
    try:
        yield runtime, session, organization, runtime._sessions[session.id].page
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_hidden_controls_do_not_hide_search_and_refs_survive_insertion(browser_session) -> None:
    runtime, session, organization, page = browser_session
    await page.set_content('<button style="display:none">Hidden</button>' * 150
                           + '<input type="search" aria-label="Search" id="query">'
                           + '<div id="result"></div><script>query.onkeydown=e=>{'
                           + 'if(e.key==="Enter")result.textContent="Correct target"}</script>')
    before = await runtime.observe(session.id, organization_id=organization, worker_id=None)
    search = next(element for element in before.elements if element.name == "Search")
    assert search.keyboard_enter_safe
    await page.evaluate('document.body.prepend(document.createElement("input"))')
    after = await runtime.interact(session.id, organization_id=organization, worker_id=None,
                                  operation="element.press_key", value="Enter",
                                  locator=BrowserLocator(strategy="observation_ref", value=search.ref))
    assert "Correct target" in after.visible_text
    assert next(element.ref for element in after.elements if element.name == "Search") == search.ref


@pytest.mark.asyncio
async def test_late_controls_become_observable_after_scroll(browser_session) -> None:
    runtime, session, organization, page = browser_session
    await page.set_content(''.join(f'<button style="display:block;height:50px">Item {i} '
                                  + 'Earlier page content ' * 8 + '</button>'
                                  for i in range(150))
                           + '<p>Details from the end of this long page</p>'
                           + '<input aria-label="Search" type="search">')
    await page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
    observed = await runtime.observe(session.id, organization_id=organization, worker_id=None)
    assert any(element.name == "Search" for element in observed.elements)
    assert "Details from the end of this long page" in observed.visible_text


@pytest.mark.asyncio
async def test_get_search_form_enter_and_post_form_protection(browser_session) -> None:
    runtime, session, organization, page = browser_session
    await page.set_content('<form><input aria-label="Search" id="query"></form><div id="result"></div>'
                           '<script>document.querySelector("form").onsubmit=e=>{e.preventDefault();'
                           'result.textContent="Results loaded"}</script>')
    observed = await runtime.observe(session.id, organization_id=organization, worker_id=None)
    ref = next(element.ref for element in observed.elements if element.name == "Search")
    after = await runtime.interact(session.id, organization_id=organization, worker_id=None,
                                  operation="element.press_key", value="Enter",
                                  locator=BrowserLocator(strategy="observation_ref", value=ref))
    assert "Results loaded" in after.visible_text
    await page.evaluate('document.querySelector("form").method="post"')
    with pytest.raises(ValueError, match="external write"):
        await runtime.interact(session.id, organization_id=organization, worker_id=None,
                               operation="element.press_key", value="Enter",
                               locator=BrowserLocator(strategy="observation_ref", value=ref))


@pytest.mark.asyncio
async def test_search_label_cannot_send_post(browser_session) -> None:
    runtime, session, organization, page = browser_session
    await page.set_content('<input type="search" id="query"><div id="result"></div>'
                           '<script>query.onkeydown=e=>{if(e.key==="Enter")'
                           'fetch("https://example.com/write",{method:"POST"})'
                           '.catch(()=>result.textContent="Write blocked")}</script>')
    observed = await runtime.observe(session.id, organization_id=organization, worker_id=None)
    ref = next(element.ref for element in observed.elements if element.element_type == "search")
    after = await runtime.interact(session.id, organization_id=organization, worker_id=None,
                                  operation="element.press_key", value="Enter",
                                  locator=BrowserLocator(strategy="observation_ref", value=ref))
    assert "Write blocked" in after.visible_text
    assert after.page_state["keyboardWriteBlocked"] is True


@pytest.mark.asyncio
async def test_key_timeout_observes_without_replay(browser_session, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    runtime, session, organization, page = browser_session
    await page.set_content('<input type="search" aria-label="Search">')
    observed = await runtime.observe(session.id, organization_id=organization, worker_id=None)
    locator = BrowserLocator(strategy="observation_ref", value=observed.elements[0].ref)
    target = runtime._locator(runtime._sessions[session.id], locator)
    press = AsyncMock(side_effect=PlaywrightTimeoutError("Response timed out"))
    monkeypatch.setattr(target, "press", press)
    monkeypatch.setattr(runtime, "_locator", lambda handle, spec: target)
    after = await runtime.interact(session.id, organization_id=organization, worker_id=None,
                                  operation="element.press_key", value="Enter", locator=locator,
                                  timeout_ms=1000)
    assert press.await_count == 1
    assert after.page_state["actionAttempt"]["status"] == "timed_out"
    assert after.id != observed.id


@pytest.mark.asyncio
async def test_key_observes_results_arriving_after_two_seconds(browser_session) -> None:
    runtime, session, organization, page = browser_session
    await page.set_content('<input type="search" id="query"><div id="result"></div>'
                           '<script>query.onkeydown=e=>{if(e.key==="Enter")'
                           'setTimeout(()=>result.textContent="Late results",2300)}</script>')
    observed = await runtime.observe(session.id, organization_id=organization, worker_id=None)
    after = await runtime.interact(session.id, organization_id=organization, worker_id=None,
                                  operation="element.press_key", value="Enter",
                                  locator=BrowserLocator(strategy="observation_ref",
                                                         value=observed.elements[0].ref))
    assert "Late results" in after.visible_text
