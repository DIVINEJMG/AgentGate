"""F50: a click, check or uncheck never sends a change to the site; the change is held, reported,
and sent only through the governed form submit (which policy can put behind approval). Reads,
links, beacons, logins and keyboard rules are unchanged. F49: an action input the provider refuses
is an invalid action to replan, never an interrupted one."""
from __future__ import annotations

import asyncio
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.context.narrator import narrate
from app.domain.actions.gateway import ActionProposal
from app.execution.browser.contracts import BrowserLocator
from app.execution.browser.policy import BrowserDomainPolicy
from app.execution.browser.runtime import BrowserRuntime
from app.execution.contracts import ExecutionProviderError, ExecutionResult
from app.execution.provider_executor import UniversalProviderExecutor
from app.execution.providers.browser import PlaywrightBrowserProvider
from app.execution.providers.registry import ProviderRegistry

sys.path.insert(0, str(Path(__file__).parent))
from test_f30_0_10 import FakeBrowserRuntime, request_for

SITE = "https://admin.test"
PAGE = """<h1>Worker Access</h1><p id="account">Test - Active</p><p id="details"></p>
<button type="button" id="disable" onclick="fetch('/api/workers/1/disable', {method: 'POST'})
  .then(() => { account.textContent = 'Test - Disabled'; })">Disable account</button>
<button type="button" id="remove" onclick="if (confirm('Remove Test?')) fetch('/api/workers/1', {method: 'DELETE'})
  .then(() => { account.textContent = 'Removed'; })">Remove worker</button>
<button type="button" id="view" onclick="fetch('/api/workers/1').then((r) => r.json())
  .then((data) => { details.textContent = data.email; })">View details</button>
<button type="button" id="track" onclick="navigator.sendBeacon('/beacon', 'clicked'); details.textContent = 'Noted'">Note</button>
<label><input type="checkbox" id="alerts" onchange="fetch('/api/alerts', {method: 'PATCH'})"> Email alerts</label>
<a href="/help">Help</a>"""


class _Site(BaseHTTPRequestHandler):
    """The admin site, served for real so every request passes AgentGate's own routing."""

    received: list[tuple[str, str]]

    def _answer(self) -> None:
        self.received.append((self.command, self.path))
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        pages = {"/workers": PAGE, "/help": "<h1>Help</h1>"}
        if self.command == "GET" and self.path in pages:
            body, kind = f"<html><body>{pages[self.path]}</body></html>", "text/html"
        elif self.command == "GET" and self.path == "/api/workers/1":
            body, kind = '{"email": "test@example.com"}', "application/json"
        else:
            body, kind = "", "text/plain"
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_DELETE = _answer

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
async def site():
    received: list[tuple[str, str]] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), type("Site", (_Site,), {"received": received}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    runtime = BrowserRuntime(headless=True)
    organization = uuid4()
    session = await runtime.create_session(
        organization_id=organization, worker_id=None, run_id=uuid4(),
        navigation_policy=BrowserDomainPolicy.from_configuration(
            {"allowedOrigins": origin, "allowPrivateNetwork": "true"}))
    try:
        await runtime.navigate(session.id, organization_id=organization, worker_id=None,
                               operation="navigation.open", url=f"{origin}/workers")
        yield SimpleNamespace(runtime=runtime, session_id=session.id, organization=organization,
                              origin=origin, received=received)
    finally:
        await runtime.shutdown()
        server.shutdown()


def _control(selector: str) -> BrowserLocator:
    return BrowserLocator(strategy="css", value=selector)


async def _act(site, operation: str, selector: str):
    return await site.runtime.interact(site.session_id, organization_id=site.organization, worker_id=None,
                                       operation=operation, locator=_control(selector))


async def _submit(site, selector: str, **kwargs):
    return await site.runtime.submit_form(site.session_id, organization_id=site.organization, worker_id=None,
                                          submit_locator=_control(selector), **kwargs)


def _writes(site) -> list[tuple[str, str]]:
    return [item for item in site.received if item[0] not in {"GET", "HEAD", "OPTIONS"}]


@pytest.mark.asyncio
async def test_a_click_that_sends_a_change_is_held_and_nothing_reaches_the_site(site) -> None:
    after = await _act(site, "element.click", "#disable")
    attempt = after.page_state["actionAttempt"]
    assert attempt["status"] == "write_held"
    assert attempt["request"] == {"method": "POST", "url": f"{site.origin}/api/workers/1/disable"}
    assert _writes(site) == []
    assert "Test - Active" in after.visible_text


@pytest.mark.asyncio
async def test_the_governed_submit_sends_the_held_change(site) -> None:
    await _act(site, "element.click", "#disable")
    after = await _submit(site, "#disable")
    assert _writes(site) == [("POST", "/api/workers/1/disable")]
    assert "Test - Disabled" in after.visible_text
    assert "actionAttempt" not in after.page_state


@pytest.mark.asyncio
async def test_a_confirmed_change_is_sent_only_by_the_submit_that_accepts_the_dialog(site) -> None:
    held = await _act(site, "element.click", "#remove")  # the dialog is dismissed by default
    assert "actionAttempt" not in held.page_state and _writes(site) == []
    after = await _submit(site, "#remove", dialog_action="accept")
    assert _writes(site) == [("DELETE", "/api/workers/1")]
    assert "Removed" in after.visible_text


@pytest.mark.asyncio
async def test_a_toggle_that_saves_itself_is_held(site) -> None:
    after = await _act(site, "element.check", "#alerts")
    assert after.page_state["actionAttempt"]["request"]["method"] == "PATCH"
    assert _writes(site) == []


@pytest.mark.asyncio
async def test_reads_links_and_beacons_are_not_held(site) -> None:
    after = await _act(site, "element.click", "#view")
    assert "test@example.com" in after.visible_text and "actionAttempt" not in after.page_state
    after = await _act(site, "element.click", "#track")  # an analytics beacon is not a change
    assert "Noted" in after.visible_text and "actionAttempt" not in after.page_state
    after = await _act(site, "element.click", "a[href='/help']")
    assert after.url == f"{site.origin}/help" and "actionAttempt" not in after.page_state
    assert [item for item in _writes(site) if item[1] != "/beacon"] == []


@pytest.mark.asyncio
async def test_the_hold_ends_with_the_click(site) -> None:
    await _act(site, "element.click", "#disable")
    handle = site.runtime._sessions[site.session_id]
    assert handle.write_guarded is False
    # The page's own later change (not caused by a click) is not held.
    await handle.page.evaluate("fetch('/api/workers/1/disable', {method: 'POST'})")
    await asyncio.sleep(0.3)
    assert _writes(site) == [("POST", "/api/workers/1/disable")]


def _held_result(operation: str) -> ExecutionResult:
    return ExecutionResult.successful(
        provider="browser", adapter="browser", adapter_version="1.0.0", operation=operation,
        output={"session": {"id": ""}, "observation": {"url": f"{SITE}/workers"},
                "actionEvidence": {"result": "not_sent", "stateChanged": False, "elementReference": "e3",
                                   "heldRequest": {"method": "POST", "url": f"{SITE}/api/workers/1/disable?name=Ada"}}},
        provider_request_id=None, started_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_a_held_change_is_reported_as_certain_and_points_to_the_governed_submit() -> None:
    provider = PlaywrightBrowserProvider(FakeBrowserRuntime())
    request = request_for(provider, operation="element.click",
                          payload={"locator": {"strategy": "observation_ref", "value": "e3"}})
    result = _held_result("element.click")
    verification = await provider.verify(request=request, result=result, configuration={}, credential=None)
    assert verification.verified is True
    assert verification.summary.startswith("Nothing was sent")
    assert "POST https://admin.test/api/workers/1/disable" in verification.summary
    assert "browser.form.submit" in verification.summary
    evidence = cast(dict, result.output)["actionEvidence"]
    assert evidence["outcome"] == "change_held"
    # Redacted for storage, the sentence stays whole (an address's query once swallowed the rest).
    assert evidence["verification"]["summary"].endswith("Do not click it again.")
    assert narrate("browser.element.click", result.output) == (
        "Held the change this control sends to the site; nothing was sent.")


@pytest.mark.asyncio
async def test_a_refused_action_input_is_an_invalid_action_not_an_interrupted_one() -> None:
    """F49: auto-accepting a dialog on a click is refused before anything is sent."""
    provider = PlaywrightBrowserProvider(FakeBrowserRuntime())
    executor = UniversalProviderExecutor(
        registry=ProviderRegistry((provider,)),
        context_loader=cast(Any, SimpleNamespace(load=AsyncMock(return_value=SimpleNamespace()))))
    proposal = ActionProposal(
        organization_id=uuid4(), agent_id=uuid4(), provider="browser", operation="element.click",
        scope="browser.element.click", resource_id="site",
        payload={"sessionId": str(uuid4()), "locator": {"strategy": "observation_ref", "value": "e3"},
                 "dialog": {"action": "accept"}},
        correlation_id="test", idempotency_key="test")
    with pytest.raises(ExecutionProviderError) as caught:
        await executor.prepare(proposal)
    assert caught.value.error.code == "validation_error" and not caught.value.error.retryable
    assert "restricted to high-risk governed operations" in caught.value.error.safe_message
    assert "Nothing was dispatched" in caught.value.error.safe_message


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "replanned"), [("validation_error", True), ("temporary_provider_error", False)])
async def test_a_chat_task_replans_a_refused_action_and_nothing_else_changes(monkeypatch, code, replanned) -> None:
    """F49: a task without a chat origin (a chat browser task) replans an action refused before
    anything was sent; other errors keep their earlier handling."""
    from app.bootstrap.settings import settings
    from app.runtime.managed import ManagedRuntimeExecutor

    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    monkeypatch.setattr("app.runtime.managed.notify_integration_work", AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr("app.runtime.managed.TransactionalOutbox", lambda _: SimpleNamespace(enqueue=enqueue))
    executor = object.__new__(ManagedRuntimeExecutor)
    executor._session = cast(Any, SimpleNamespace(commit=AsyncMock()))
    item = SimpleNamespace(id=uuid4(), organization_id=uuid4(), job_id=uuid4(), correlation_id="test",
                           payload={"runtime": {}})
    step = SimpleNamespace(status="pending", output={})
    error = ExecutionProviderError(code=code, retryable=False, provider="browser", operation="element.click",
                                   correlation_id="test", safe_message="Refused. Nothing was dispatched.")
    args = {"item": item, "run": SimpleNamespace(id=uuid4(), status="running"), "step": step, "current_step": 1,
            "job": SimpleNamespace(), "worker": SimpleNamespace()}
    if not replanned:
        with pytest.raises(ExecutionProviderError):
            await executor._integration_guarded_action(AsyncMock(side_effect=error), **args)
        return
    outcome = await executor._integration_guarded_action(AsyncMock(side_effect=error), **args)
    assert outcome.state == "continue" and outcome.continuation_phase == "plan"
    assert step.status == "failed" and step.output["data"]["integrationFailure"]["executed"] is False
    assert item.payload["runtime"]["currentStep"] == 2
    enqueue.assert_awaited_once()
