from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.execution.browser.contracts import (
    BrowserElement,
    BrowserFrame,
    BrowserLocator,
    BrowserObservation,
    BrowserSession,
)
from app.execution.contracts import (
    ExecutionPreferences,
    ExecutionRequest,
    ProviderRuntimeContext,
    ResourceDescriptor,
)
from app.execution.providers.browser import (
    BrowserExecutionProvider,
    PlaywrightBrowserProvider,
)
from app.execution.providers.registry import ProviderRegistry
from app.execution.providers.resolver import ExecutionResolver


class FakeBrowserRuntime:
    def __init__(self) -> None:
        self.sessions: dict[UUID, BrowserSession] = {}
        self.terminated: list[UUID] = []
        self.operations: list[tuple[str, str]] = []

    async def health(self) -> bool:
        return True

    async def shutdown(self) -> None:
        self.sessions.clear()

    async def create_session(
        self,
        *,
        organization_id,
        worker_id,
        run_id,
        ttl_seconds=1800,
        navigation_policy=None,
    ) -> BrowserSession:
        del navigation_policy
        now = datetime.now(UTC)
        session = BrowserSession(
            id=uuid4(),
            organization_id=organization_id,
            worker_id=worker_id,
            run_id=run_id,
            browser_context_id=f"ctx_{uuid4().hex}",
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            status="active",
        )
        self.sessions[session.id] = session
        return session

    async def resume(self, session_id, *, organization_id, worker_id) -> BrowserSession:
        session = self.sessions[session_id]
        if session.organization_id != organization_id:
            raise PermissionError("Cross-organization browser session access denied.")
        if session.worker_id != worker_id:
            raise PermissionError("Cross-worker browser session access denied.")
        return session

    async def close(self, session_id) -> BrowserSession:
        return self.sessions.pop(session_id)

    async def expire(self, session_id) -> BrowserSession:
        return self.sessions.pop(session_id)

    async def terminate(self, session_id) -> BrowserSession:
        self.terminated.append(session_id)
        return self.sessions.pop(session_id)

    def _observation(self, session_id: UUID, url: str) -> BrowserObservation:
        return BrowserObservation(
            id=f"obs_{uuid4().hex}",
            session_id=session_id,
            url=url,
            title="Aduoryn Browser Test",
            visible_text="Open settings Submit",
            aria_snapshot='- button "Submit"',
            dom_snapshot="<main><button>Submit</button></main>",
            elements=(
                BrowserElement(
                    ref="e1",
                    tag="button",
                    role="button",
                    name="Submit",
                    text="Submit",
                    element_type=None,
                    value=None,
                    checked=None,
                    selected=None,
                    disabled=False,
                    href=None,
                ),
            ),
            forms=("form:1",),
            links=(),
            buttons=("e1",),
            inputs=(),
            frames=(
                BrowserFrame(
                    name=None,
                    url=url,
                    origin="https://example.com",
                    is_main=True,
                ),
            ),
            page_state={"loadState": "complete"},
            observed_at=datetime.now(UTC),
        )

    async def observe(self, session_id, *, organization_id, worker_id):
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        return self._observation(session_id, "https://example.com/current")

    async def navigate(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        operation,
        url=None,
        locator=None,
        timeout_ms=30000,
    ):
        del locator, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.operations.append(("navigate", operation))
        return self._observation(session_id, url or "https://example.com/current")

    async def interact(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        operation,
        locator=None,
        value=None,
        timeout_ms=15000,
    ):
        del locator, value, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.operations.append(("interact", operation))
        return self._observation(session_id, "https://example.com/current")

    async def fill_form(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        fields,
        timeout_ms=15000,
    ):
        del fields, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.operations.append(("form", "form.fill"))
        return self._observation(session_id, "https://example.com/current")

    async def submit_form(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        form_ref=None,
        submit_locator=None,
        timeout_ms=15000,
    ):
        del form_ref, submit_locator, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.operations.append(("form", "form.submit"))
        return self._observation(session_id, "https://example.com/current")

    async def authenticate(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        bindings,
        credentials,
        form_ref=None,
        submit_locator=None,
        failure_text=None,
        success_url_contains=None,
        timeout_ms=15000,
    ):
        del (
            bindings,
            credentials,
            form_ref,
            submit_locator,
            failure_text,
            success_url_contains,
            timeout_ms,
        )
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.operations.append(("auth", "auth.login"))
        return self._observation(session_id, "https://example.com/current")


def resource(provider: PlaywrightBrowserProvider) -> ResourceDescriptor:
    return ResourceDescriptor(
        id="integration:browser-test",
        provider="browser",
        resource_type="origin",
        external_id="origin:https://example.com",
        display_name="Browser Test",
        metadata={"origin": "https://example.com"},
        health="healthy",
        available_capabilities=tuple(item.scope for item in provider.manifest.capabilities),
        web_url="https://example.com",
        configuration={"allowedOrigins": "https://example.com"},
    )


def request_for(
    provider: PlaywrightBrowserProvider,
    *,
    operation: str,
    payload: dict[str, object],
    organization_id=None,
    worker_id=None,
) -> ExecutionRequest:
    capability = next(
        item for item in provider.manifest.capabilities if item.operation == operation
    )
    return ExecutionRequest(
        organization_id=organization_id or uuid4(),
        worker_id=worker_id or uuid4(),
        agent_id=uuid4(),
        job_id=uuid4(),
        work_item_id=uuid4(),
        run_id=uuid4(),
        capability=capability,
        resource=resource(provider),
        operation=operation,
        input=payload,
        correlation_id=f"f30-{operation}",
        idempotency_key=f"f30-{operation}-{uuid4()}",
        execution_preferences=ExecutionPreferences(
            preferred_provider_kinds=("browser",),
            allow_fallback=False,
        ),
    )


def test_f30_browser_is_first_class_versioned_execution_provider() -> None:
    provider = PlaywrightBrowserProvider(FakeBrowserRuntime())
    assert isinstance(provider, BrowserExecutionProvider)
    assert provider.manifest.provider == "browser"
    assert provider.manifest.kind == "browser"
    assert provider.manifest.version == "1.0.0"

    operations = {item.operation for item in provider.manifest.capabilities}
    assert {
        "page.observe",
        "navigation.open",
        "navigation.back",
        "navigation.forward",
        "navigation.reload",
        "navigation.follow_link",
        "element.click",
        "element.type",
        "element.clear",
        "element.select",
        "element.check",
        "element.uncheck",
        "element.press_key",
        "page.scroll",
        "element.hover",
    } <= operations


@pytest.mark.asyncio
async def test_f30_universal_resolver_selects_browser_without_special_case() -> None:
    runtime = FakeBrowserRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    registry = ProviderRegistry((provider,))
    resolver = ExecutionResolver(registry)
    request = request_for(
        provider,
        operation="navigation.open",
        payload={"url": "https://example.com"},
    )
    context = ProviderRuntimeContext(
        configuration={"allowedOrigins": "https://example.com"},
        credential=None,
        resource=request.resource,
    )

    resolved = await resolver.resolve(request, contexts={"browser": context})

    assert resolved.provider is provider
    assert resolved.context is context


@pytest.mark.asyncio
async def test_f30_open_navigation_creates_isolated_governed_session() -> None:
    runtime = FakeBrowserRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    request = request_for(
        provider,
        operation="navigation.open",
        payload={"url": "https://example.com"},
    )

    result = await provider.execute(request=request, configuration={}, credential=None)

    output = result.output
    assert isinstance(output, dict)
    session = output["session"]
    observation = output["observation"]
    assert isinstance(session, dict)
    assert isinstance(observation, dict)
    assert session["organizationId"] == str(request.organization_id)
    assert session["workerId"] == str(request.worker_id)
    assert session["browserContextId"].startswith("ctx_")
    assert observation["url"] == "https://example.com"
    assert observation["buttons"] == ["e1"]
    assert runtime.operations == [("navigate", "navigation.open")]


@pytest.mark.asyncio
async def test_f30_session_cannot_cross_organization_or_worker() -> None:
    runtime = FakeBrowserRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    organization_id = uuid4()
    worker_id = uuid4()
    initial = request_for(
        provider,
        operation="navigation.open",
        payload={"url": "https://example.com"},
        organization_id=organization_id,
        worker_id=worker_id,
    )
    first = await provider.execute(request=initial, configuration={}, credential=None)
    assert isinstance(first.output, dict)
    session_data = first.output["session"]
    assert isinstance(session_data, dict)
    session_id = str(session_data["id"])

    wrong_org = request_for(
        provider,
        operation="page.observe",
        payload={"sessionId": session_id},
        organization_id=uuid4(),
        worker_id=worker_id,
    )
    with pytest.raises(Exception, match="access was denied"):
        await provider.execute(request=wrong_org, configuration={}, credential=None)

    # A foreign tenant cannot use a guessed session ID to terminate its owner.
    assert UUID(session_id) not in runtime.terminated
    assert UUID(session_id) in runtime.sessions


@pytest.mark.asyncio
async def test_f30_locator_model_is_browser_neutral() -> None:
    provider = PlaywrightBrowserProvider(FakeBrowserRuntime())
    session_id = str(uuid4())
    strategies = (
        {"strategy": "observation_ref", "value": "e1", "observationId": "obs_1"},
        {"strategy": "role", "value": "button", "name": "Save"},
        {"strategy": "label", "value": "Email"},
        {"strategy": "text", "value": "Continue"},
        {"strategy": "css", "value": "[data-testid='save']"},
    )
    for locator in strategies:
        normalized = await provider.normalize_input(
            operation="element.click",
            input={"sessionId": session_id, "locator": locator},
        )
        parsed = BrowserLocator.from_mapping(normalized["locator"])
        assert parsed.strategy == locator["strategy"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "extra"),
    [
        ("navigation.back", {}),
        ("navigation.forward", {}),
        ("navigation.reload", {}),
        ("navigation.follow_link", {"locator": {"strategy": "text", "value": "Next"}}),
        ("element.click", {"locator": {"strategy": "role", "value": "button"}}),
        (
            "element.type",
            {"locator": {"strategy": "label", "value": "Name"}, "value": "Divine"},
        ),
        ("element.clear", {"locator": {"strategy": "label", "value": "Name"}}),
        (
            "element.select",
            {"locator": {"strategy": "label", "value": "Country"}, "value": "NG"},
        ),
        ("element.check", {"locator": {"strategy": "label", "value": "Agree"}}),
        ("element.uncheck", {"locator": {"strategy": "label", "value": "Agree"}}),
        (
            "element.press_key",
            {"locator": {"strategy": "label", "value": "Search"}, "value": "Enter"},
        ),
        ("page.scroll", {"value": 500}),
        ("element.hover", {"locator": {"strategy": "text", "value": "Menu"}}),
    ],
)
async def test_f30_navigation_and_interaction_primitives_are_normalized(
    operation: str,
    extra: dict[str, object],
) -> None:
    runtime = FakeBrowserRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    organization_id = uuid4()
    worker_id = uuid4()
    session = await runtime.create_session(
        organization_id=organization_id,
        worker_id=worker_id,
        run_id=uuid4(),
    )
    payload = {"sessionId": str(session.id), **extra}
    normalized = await provider.normalize_input(operation=operation, input=payload)
    request = request_for(
        provider,
        operation=operation,
        payload=normalized,
        organization_id=organization_id,
        worker_id=worker_id,
    )

    result = await provider.execute(request=request, configuration={}, credential=None)
    verification = await provider.verify(
        request=request,
        result=result,
        configuration={},
        credential=None,
    )

    assert result.status == "success"
    assert verification.verified is True


def test_f30_vercel_auto_deploy_remains_disabled() -> None:
    root = Path(__file__).resolve().parents[2]
    vercel = (root / "vercel.json").read_text(encoding="utf-8")
    assert '"deploymentEnabled": false' in vercel


@pytest.mark.asyncio
async def test_f30_cancelled_execution_terminates_browser_session() -> None:
    class CancellingRuntime(FakeBrowserRuntime):
        async def interact(self, *args, **kwargs):
            raise asyncio.CancelledError

    runtime = CancellingRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    organization_id = uuid4()
    worker_id = uuid4()
    session = await runtime.create_session(
        organization_id=organization_id,
        worker_id=worker_id,
        run_id=uuid4(),
    )
    request = request_for(
        provider,
        operation="element.click",
        payload={
            "sessionId": str(session.id),
            "locator": {"strategy": "role", "value": "button", "name": "Save"},
        },
        organization_id=organization_id,
        worker_id=worker_id,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.execute(request=request, configuration={}, credential=None)

    assert session.id in runtime.terminated


@pytest.mark.asyncio
async def test_f30_session_cannot_cross_worker_boundary() -> None:
    runtime = FakeBrowserRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    organization_id = uuid4()
    owner_worker = uuid4()
    session = await runtime.create_session(
        organization_id=organization_id,
        worker_id=owner_worker,
        run_id=uuid4(),
    )
    request = request_for(
        provider,
        operation="page.observe",
        payload={"sessionId": str(session.id)},
        organization_id=organization_id,
        worker_id=uuid4(),
    )

    with pytest.raises(Exception, match="access was denied"):
        await provider.execute(request=request, configuration={}, credential=None)

    assert session.id not in runtime.terminated
    assert session.id in runtime.sessions
