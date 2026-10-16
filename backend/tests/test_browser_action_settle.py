"""F48: after a browser action, the page is observed once what the action started has finished (its
requests, a page change sent by script, content drawn after load), and never later than the limit."""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest

from app.execution.browser.contracts import BrowserLocator
from app.execution.browser.credentials import BrowserCredentialBundle
from app.execution.browser.policy import BrowserDomainPolicy
from app.execution.browser.runtime import ACTION_SETTLE_MAX_SECONDS, BrowserRuntime

SITE = "https://app.test"
PAGES = {
    # A login sent by script, which opens the dashboard only after the answer arrives.
    "/login": """<form id="login"><input type="email" name="username" aria-label="Email">
<input type="password" name="password" aria-label="Password"><button type="submit">Sign in</button></form>
<script>login.addEventListener('submit', async (event) => {
  event.preventDefault();
  await fetch('/session', {method: 'POST', body: new FormData(login)});
  location.href = '/app';
});</script>""",
    # A dashboard drawn by script: empty until its data has arrived.
    "/app": """<div id="app"></div><script>fetch('/data').then((r) => r.json()).then((data) => {
  app.innerHTML = '<h1>Dashboard</h1><p>' + data.inquiries + '</p>';
});</script>""",
    "/panel": """<button id="load" onclick="fetch('/data').then((r) => r.json())
  .then((data) => { result.textContent = data.inquiries; })">Load inquiries</button><p id="result"></p>""",
    "/busy": """<button id="noop">Nothing</button><ul id="ticker"></ul><script>
setInterval(() => ticker.append(document.createElement('li')), 100);</script>""",
}
DATA_SECONDS = 0.8


async def _serve(route) -> None:
    path = route.request.url.removeprefix(SITE).split("?")[0]
    if path in PAGES:
        await route.fulfill(content_type="text/html", body=f"<html><body>{PAGES[path]}</body></html>")
    elif path == "/data":
        await asyncio.sleep(DATA_SECONDS)
        await route.fulfill(content_type="application/json", body='{"inquiries": "INBOX EMPTY"}')
    else:
        await asyncio.sleep(0.3)
        await route.fulfill(status=200, body="ok")


@pytest.fixture
async def site():
    runtime = BrowserRuntime(headless=True)
    organization = uuid4()
    session = await runtime.create_session(
        organization_id=organization, worker_id=None, run_id=uuid4(),
        navigation_policy=BrowserDomainPolicy.from_configuration({"allowedOrigins": SITE}))
    # Served inside the test, ahead of the session's own routing.
    await runtime._sessions[session.id].context.route(f"{SITE}/**", _serve)
    try:
        yield runtime, session.id, organization
    finally:
        await runtime.shutdown()


async def _open(runtime: BrowserRuntime, session_id, organization, path: str):
    return await runtime.navigate(session_id, organization_id=organization, worker_id=None,
                                  operation="navigation.open", url=f"{SITE}{path}")


def _ref(observation, name: str) -> BrowserLocator:
    element = next(item for item in observation.elements if name in (item.name, item.text))
    return BrowserLocator(strategy="observation_ref", value=element.ref, observation_id=observation.id)


@pytest.mark.asyncio
async def test_a_script_login_is_observed_on_the_drawn_dashboard(site) -> None:
    runtime, session_id, organization = site
    page = await _open(runtime, session_id, organization, "/login")
    after = await runtime.authenticate(
        session_id, organization_id=organization, worker_id=None,
        bindings=((_ref(page, "Email"), "username"), (_ref(page, "Password"), "password")),
        credentials=BrowserCredentialBundle.from_secret('{"username": "ada@example.com", "password": "s3cret!"}'),
        submit_locator=_ref(page, "Sign in"))
    assert after.url == f"{SITE}/app"
    assert "INBOX EMPTY" in after.visible_text  # not the empty page seen before its script ran


@pytest.mark.asyncio
async def test_a_click_is_observed_once_its_request_answered(site) -> None:
    runtime, session_id, organization = site
    page = await _open(runtime, session_id, organization, "/panel")
    after = await runtime.interact(session_id, organization_id=organization, worker_id=None,
                                   operation="element.click", locator=_ref(page, "Load inquiries"))
    assert "INBOX EMPTY" in after.visible_text


@pytest.mark.asyncio
async def test_a_quiet_action_is_not_slowed_and_a_busy_page_is_not_waited_for_past_the_limit(site) -> None:
    runtime, session_id, organization = site
    page = await _open(runtime, session_id, organization, "/panel")
    started = time.monotonic()
    await runtime.interact(session_id, organization_id=organization, worker_id=None,
                           operation="element.hover", locator=_ref(page, "Load inquiries"))
    assert time.monotonic() - started < 1.5

    page = await _open(runtime, session_id, organization, "/busy")  # changes forever
    started = time.monotonic()
    await runtime.interact(session_id, organization_id=organization, worker_id=None,
                           operation="element.click", locator=_ref(page, "Nothing"))
    assert time.monotonic() - started < ACTION_SETTLE_MAX_SECONDS + 1.5
