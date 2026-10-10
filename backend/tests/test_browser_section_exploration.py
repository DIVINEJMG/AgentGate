from __future__ import annotations

from uuid import uuid4

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from app.execution.browser.contracts import BrowserLocator, BrowserObservation
from app.execution.browser.observation import observe_page
from app.execution.browser.runtime import BrowserRuntime
from app.execution.providers.browser import CAPABILITIES


def test_browser_tools_expose_focused_read_and_horizontal_scroll() -> None:
    by_scope = {capability.scope: capability for capability in CAPABILITIES}
    read = by_scope["browser.page.read"].input_schema["properties"]
    scroll = by_scope["browser.page.scroll"].input_schema["properties"]
    assert isinstance(read, dict)
    assert isinstance(scroll, dict)
    assert "focusText" in read
    assert "focusText" in scroll
    assert "axis" in scroll
    assert "locator" in scroll


def _focused_section(observation: BrowserObservation) -> dict[str, object]:
    focused = observation.page_state.get("focusedSection")
    assert isinstance(focused, dict)
    return focused


@pytest.mark.asyncio
async def test_press_key_observes_delayed_same_page_results() -> None:
    runtime = BrowserRuntime(headless=True)
    organization_id = uuid4()
    try:
        try:
            session = await runtime.create_session(
                organization_id=organization_id, worker_id=None, run_id=uuid4()
            )
        except PlaywrightError as error:
            pytest.skip(f"Playwright Chromium is unavailable: {error}")
            return
        page = runtime._sessions[session.id].page
        await page.set_content(
            '<input aria-label="Search" id="query"><div id="results"></div>'
            '<script>document.querySelector("#query").addEventListener("keydown", event => {'
            'if (event.key === "Enter") setTimeout(() => {'
            'document.querySelector("#results").textContent = "Search results loaded";'
            '}, 150); });</script>'
        )
        before = await runtime.observe(
            session.id, organization_id=organization_id, worker_id=None
        )
        search = next(element for element in before.elements if element.name == "Search")
        after = await runtime.interact(
            session.id,
            organization_id=organization_id,
            worker_id=None,
            operation="element.press_key",
            locator=BrowserLocator(strategy="observation_ref", value=search.ref),
            value="Enter",
        )
        assert after.url == before.url
        assert "Search results loaded" in after.visible_text
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_unwrapped_search_submit_is_observed_and_clickable() -> None:
    runtime = BrowserRuntime(headless=True)
    organization_id = uuid4()
    try:
        try:
            session = await runtime.create_session(
                organization_id=organization_id, worker_id=None, run_id=uuid4()
            )
        except PlaywrightError as error:
            pytest.skip(f"Playwright Chromium is unavailable: {error}")
            return
        page = runtime._sessions[session.id].page
        await page.set_content(
            '<input aria-label="Search" value="Real Madrid">'
            '<input type="submit" role="textbox" aria-label="Search Sports, Teams or Players" '
            'onclick="document.querySelector(\'#results\').textContent=\'Real Madrid results\'">'
            '<div id="results"></div>'
        )
        before = await runtime.observe(
            session.id, organization_id=organization_id, worker_id=None
        )
        submit = next(element for element in before.elements if element.element_type == "submit")
        assert submit.role == "button"
        after = await runtime.interact(
            session.id,
            organization_id=organization_id,
            worker_id=None,
            operation="element.click",
            locator=BrowserLocator(strategy="observation_ref", value=submit.ref),
        )
        assert "Real Madrid results" in after.visible_text
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_same_page_carousel_and_profile_are_observable() -> None:
    async with async_playwright() as playwright:
        browser = None
        try:
            browser = await playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            pytest.skip(f"Playwright Chromium is unavailable: {error}")
            return
        try:
            page = await browser.new_page()
            await page.set_content(
                """
                <section>
                  <h2>Leadership</h2>
                  <div id="cards">Rotimi Ibrahim — Group Managing Director</div>
                  <button aria-label="Next leadership slide" onclick="nextSlide()">Next</button>
                  <div id="profile" hidden></div>
                </section>
                <script>
                  function nextSlide() {
                    document.querySelector('#cards').innerHTML =
                      '<div onclick="openProfile()"><h3>Sunday Aikulola</h3>' +
                      '<span>Production Manager</span></div>';
                  }
                  function openProfile() {
                    const profile = document.querySelector('#profile');
                    profile.hidden = false;
                    profile.textContent = 'Sunday Aikulola has over 20 years of experience.';
                  }
                </script>
                """
            )
            first = await observe_page(page, session_id=uuid4(), focus_text="Leadership")
            focused = _focused_section(first)
            assert "Rotimi Ibrahim" in str(focused["text"])
            controls = focused["controls"]
            assert isinstance(controls, list)
            assert any(
                isinstance(control, dict) and control.get("name") == "Next leadership slide"
                for control in controls
            )

            await page.get_by_role("button", name="Next leadership slide").click()
            second = await observe_page(page, session_id=uuid4(), focus_text="Leadership")
            assert second.url == first.url
            assert "Sunday Aikulola" in str(_focused_section(second)["text"])

            await page.get_by_text("Sunday Aikulola", exact=True).click()
            profile = await observe_page(page, session_id=uuid4(), focus_text="Leadership")
            assert "over 20 years" in str(_focused_section(profile)["text"])
        finally:
            if browser is not None:
                await browser.close()


@pytest.mark.asyncio
async def test_browser_scroll_can_advance_an_observed_horizontal_container() -> None:
    runtime = BrowserRuntime(headless=True)
    organization_id = uuid4()
    try:
        try:
            session = await runtime.create_session(
                organization_id=organization_id, worker_id=None, run_id=uuid4()
            )
        except PlaywrightError as error:
            pytest.skip(f"Playwright Chromium is unavailable: {error}")
            return
        page = runtime._sessions[session.id].page
        await page.set_content(
            '<section><h2>Leadership</h2>'
            '<div id="strip" style="width:100px;overflow-x:auto;white-space:nowrap">'
            '<span style="display:inline-block;width:600px">More views</span></div>'
            '</section>'
        )

        await runtime.interact(
            session.id,
            organization_id=organization_id,
            worker_id=None,
            operation="page.scroll",
            locator=BrowserLocator(strategy="css", value="#strip"),
            value=200,
            axis="horizontal",
        )

        position = await page.locator("#strip").evaluate("el => el.scrollLeft")
        assert position > 0

        await runtime.interact(
            session.id,
            organization_id=organization_id,
            worker_id=None,
            operation="page.scroll",
            focus_text="Leadership",
            value=150,
            axis="horizontal",
        )
        next_position = await page.locator("#strip").evaluate("el => el.scrollLeft")
        assert next_position > position
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_timed_out_profile_target_keeps_session_for_replanning() -> None:
    runtime = BrowserRuntime(headless=True)
    organization_id = uuid4()
    try:
        try:
            session = await runtime.create_session(
                organization_id=organization_id, worker_id=None, run_id=uuid4()
            )
        except PlaywrightError as error:
            pytest.skip(f"Playwright Chromium is unavailable: {error}")
            return
        page = runtime._sessions[session.id].page
        await page.set_content(
            '<section><h2>Leadership</h2><div onclick="this.dataset.opened=\'yes\'">'
            '<h3>Rotimi Ibrahim</h3><p>Read Profile →</p></div></section>'
        )
        observation = await runtime.navigate(
            session.id,
            organization_id=organization_id,
            worker_id=None,
            operation="navigation.follow_link",
            locator=BrowserLocator(strategy="text", value="READ PROFILE"),
            timeout_ms=1000,
        )
        assert observation.page_state["actionAttempt"] == {
            "status": "timed_out",
            "operation": "navigation.follow_link",
        }
        assert (await runtime.resume(
            session.id, organization_id=organization_id, worker_id=None
        )).status == "active"
        await runtime.navigate(
            session.id,
            organization_id=organization_id,
            worker_id=None,
            operation="navigation.follow_link",
            locator=BrowserLocator(strategy="text", value="Rotimi Ibrahim"),
        )
        assert await page.locator("h3").evaluate(
            "el => el.parentElement.dataset.opened"
        ) == "yes"
    finally:
        await runtime.shutdown()
