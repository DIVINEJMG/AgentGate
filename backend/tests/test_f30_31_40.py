from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from playwright.async_api import Browser, BrowserContext, Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.execution.browser.contracts import (
    BrowserDownload,
    BrowserLocator,
    BrowserObservation,
    BrowserSession,
    BrowserVerificationProbe,
)
from app.execution.browser.egress import evaluate_browser_egress
from app.execution.browser.errors import (
    BrowserDetachedFrame,
    BrowserDownloadFailure,
    BrowserElementNotFound,
    BrowserRuntimeLimitExceeded,
    BrowserStaleObservation,
)
from app.execution.browser.observation import (
    MAX_ARIA_SNAPSHOT,
    MAX_DOM_SNAPSHOT,
    MAX_ELEMENTS,
    MAX_VISIBLE_TEXT,
)
from app.execution.browser.policy import BrowserDomainPolicy
from app.execution.browser.runtime import BrowserRuntime
from app.execution.browser.verification import BrowserVerificationExpectation
from app.execution.contracts import (
    ExecutionError,
    ExecutionPreferences,
    ExecutionProviderError,
    ExecutionRequest,
    ResourceDescriptor,
)
from app.execution.providers.browser import (
    MAX_ACTION_TIMEOUT_MS,
    MAX_DOWNLOAD_BYTES,
    MAX_NAVIGATION_TIMEOUT_MS,
    MAX_SESSION_TTL_SECONDS,
    PlaywrightBrowserProvider,
    _bounded_int,
)
from app.execution.recovery import RecoveryPolicy
from app.realtime.contracts import REALTIME_EVENT_TYPES


class IsolationPage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.handlers: dict[str, object] = {}
        self.closed = False

    def on(self, event: str, callback) -> None:
        self.handlers[event] = callback

    def is_closed(self) -> bool:
        return self.closed


class IsolationContext:
    def __init__(self) -> None:
        self.cookie_jar: dict[str, str] = {}
        self.local_storage: dict[str, str] = {}
        self.handlers: dict[str, object] = {}
        self.closed = False

    async def new_page(self):
        return cast(Page, IsolationPage())

    async def route(self, pattern: str, callback) -> None:
        del pattern, callback

    def on(self, event: str, callback) -> None:
        self.handlers[event] = callback

    async def close(self) -> None:
        self.closed = True


class IsolationBrowser:
    def __init__(self) -> None:
        self.contexts: list[IsolationContext] = []

    async def new_context(self):
        context = IsolationContext()
        self.contexts.append(context)
        return cast(BrowserContext, context)


class IsolationRuntime(BrowserRuntime):
    def __init__(self, browser: IsolationBrowser) -> None:
        super().__init__()
        self._test_browser = browser

    async def _ensure_browser(self) -> Browser:
        return cast(Browser, self._test_browser)


class HardeningRuntime:
    def __init__(self) -> None:
        self.sessions: dict[UUID, BrowserSession] = {}
        self.failure: Exception | None = None
        self.failed_sessions: list[UUID] = []

    async def health(self) -> bool:
        return True

    async def shutdown(self) -> None:
        self.sessions.clear()

    async def create_session(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        ttl_seconds: int = 1800,
        navigation_policy: BrowserDomainPolicy | None = None,
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
            current_url="https://portal.example.test/home",
            current_origin="https://portal.example.test",
        )
        self.sessions[session.id] = session
        return session

    async def resume(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> BrowserSession:
        session = self.sessions[session_id]
        if session.organization_id != organization_id:
            raise PermissionError("Cross-organization browser session access denied.")
        if session.worker_id != worker_id:
            raise PermissionError("Cross-worker browser session access denied.")
        if session.status != "active":
            raise LookupError("Browser session is not active.")
        return session

    async def close(self, session_id: UUID) -> BrowserSession:
        return self.sessions.pop(session_id)

    async def expire(self, session_id: UUID) -> BrowserSession:
        return self.sessions.pop(session_id)

    async def terminate(self, session_id: UUID) -> BrowserSession:
        return self.sessions.pop(session_id)

    async def fail(self, session_id: UUID) -> BrowserSession:
        session = self.sessions[session_id]
        failed = replace(session, status="failed")
        self.sessions[session_id] = failed
        self.failed_sessions.append(session_id)
        return failed

    def _raise_failure(self) -> None:
        if self.failure is not None:
            raise self.failure

    def observation(self, session_id: UUID) -> BrowserObservation:
        return BrowserObservation(
            id=f"obs_{uuid4().hex}",
            session_id=session_id,
            url="https://portal.example.test/home",
            title="Portal",
            visible_text="Ready",
            aria_snapshot='- heading "Ready"',
            dom_snapshot="<main>Ready</main>",
            elements=(),
            frames=(),
            page_state={"loadState": "complete"},
            observed_at=datetime.now(UTC),
        )

    async def observe(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> BrowserObservation:
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        return self.observation(session_id)

    async def navigate(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        operation: str,
        url: str | None = None,
        locator: BrowserLocator | None = None,
        timeout_ms: int = 30_000,
    ) -> BrowserObservation:
        del operation, url, locator, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self._raise_failure()
        return self.observation(session_id)

    async def interact(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        operation: str,
        locator: BrowserLocator | None = None,
        value: object = None,
        dialog_action: str | None = None,
        prompt_text: str | None = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        del operation, locator, value, dialog_action, prompt_text, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self._raise_failure()
        return self.observation(session_id)

    async def fill_form(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        fields: tuple[tuple[BrowserLocator, object], ...],
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        del fields, timeout_ms
        return await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )

    async def submit_form(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        form_ref: str | None = None,
        submit_locator: BrowserLocator | None = None,
        dialog_action: str | None = None,
        prompt_text: str | None = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        del form_ref, submit_locator, dialog_action, prompt_text, timeout_ms
        self._raise_failure()
        return await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )

    async def authenticate(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        bindings,
        credentials,
        form_ref: str | None = None,
        submit_locator: BrowserLocator | None = None,
        failure_text: str | None = None,
        success_url_contains: str | None = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        del (
            bindings,
            credentials,
            form_ref,
            submit_locator,
            failure_text,
            success_url_contains,
            timeout_ms,
        )
        self._raise_failure()
        return await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )

    async def screenshot(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> bytes:
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        return b"png"

    async def upload_file(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        locator: BrowserLocator,
        name: str,
        media_type: str,
        content: bytes,
        dialog_action: str | None = None,
        prompt_text: str | None = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        del locator, name, media_type, content, dialog_action, prompt_text, timeout_ms
        self._raise_failure()
        return await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )

    async def download_file(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        locator: BrowserLocator,
        timeout_ms: int = 30_000,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> tuple[BrowserDownload, BrowserObservation]:
        del locator, timeout_ms, max_bytes
        self._raise_failure()
        observation = await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        return (
            BrowserDownload(
                name="download.txt",
                source_url="https://portal.example.test/download.txt",
                content=b"download",
                media_type="text/plain",
            ),
            observation,
        )

    async def verify_browser_state(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        expectation: BrowserVerificationExpectation,
        result_output: dict[str, object],
    ) -> BrowserVerificationProbe:
        del result_output
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        verified = expectation.expected_text == "Ready"
        return BrowserVerificationProbe(
            verified=verified,
            details={"expectedText": verified},
        )


def browser_resource(provider: PlaywrightBrowserProvider) -> ResourceDescriptor:
    return ResourceDescriptor(
        id="browser:origin:https://portal.example.test",
        provider="browser",
        resource_type="origin",
        external_id="origin:https://portal.example.test",
        display_name="Portal",
        metadata={"origin": "https://portal.example.test"},
        health="healthy",
        available_capabilities=tuple(
            capability.scope for capability in provider.manifest.capabilities
        ),
        web_url="https://portal.example.test/home",
        configuration={"allowedOrigins": "https://portal.example.test"},
    )


def request_for(
    provider: PlaywrightBrowserProvider,
    *,
    operation: str,
    input: dict[str, object],
    organization_id: UUID | None = None,
    worker_id: UUID | None = None,
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
        resource=browser_resource(provider),
        operation=operation,
        input=input,
        correlation_id=f"f30-31-40-{uuid4()}",
        idempotency_key=f"idem-{uuid4()}",
        execution_preferences=ExecutionPreferences(
            preferred_provider_kinds=("browser",),
            allow_fallback=False,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (BrowserStaleObservation("stale"), "stale_observation"),
        (BrowserDetachedFrame("detached"), "detached_frame"),
        (BrowserElementNotFound("missing"), "element_not_found"),
        (BrowserDownloadFailure("download failed"), "download_failure"),
        (BrowserRuntimeLimitExceeded("limit"), "runtime_limit_exceeded"),
    ),
)
async def test_f30_31_browser_errors_are_normalized(
    failure: Exception,
    expected_code: str,
) -> None:
    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    org = uuid4()
    worker = uuid4()
    session = await runtime.create_session(
        organization_id=org,
        worker_id=worker,
        run_id=uuid4(),
    )
    runtime.failure = failure
    request = request_for(
        provider,
        operation="element.click",
        input={
            "sessionId": str(session.id),
            "locator": {"strategy": "role", "value": "button", "name": "Save"},
        },
        organization_id=org,
        worker_id=worker,
    )

    with pytest.raises(ExecutionProviderError) as captured:
        await provider.execute(
            request=request,
            configuration=request.resource.configuration,
            credential=None,
        )

    assert captured.value.error.code == expected_code
    assert captured.value.error.provider == "browser"
    assert captured.value.error.operation == request.operation
    assert captured.value.error.correlation_id == request.correlation_id
    assert captured.value.error.retryable is False


@pytest.mark.asyncio
async def test_f30_31_navigation_timeout_is_retryable_but_side_effect_timeout_is_not() -> None:
    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    runtime.failure = PlaywrightTimeoutError("navigation timeout")
    navigation = request_for(
        provider,
        operation="navigation.open",
        input={"url": "https://portal.example.test/home"},
    )

    with pytest.raises(ExecutionProviderError) as captured:
        await provider.execute(
            request=navigation,
            configuration=navigation.resource.configuration,
            credential=None,
        )
    assert captured.value.error.code == "navigation_timeout"
    assert captured.value.error.retryable is True

    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    org = uuid4()
    worker = uuid4()
    session = await runtime.create_session(
        organization_id=org,
        worker_id=worker,
        run_id=uuid4(),
    )
    runtime.failure = PlaywrightTimeoutError("side effect timeout")
    click = request_for(
        provider,
        operation="element.click",
        input={
            "sessionId": str(session.id),
            "locator": {"strategy": "role", "value": "button", "name": "Save"},
        },
        organization_id=org,
        worker_id=worker,
    )
    with pytest.raises(ExecutionProviderError) as captured_click:
        await provider.execute(
            request=click,
            configuration=click.resource.configuration,
            credential=None,
        )
    assert captured_click.value.error.code == "timeout"
    assert captured_click.value.error.retryable is False


@pytest.mark.asyncio
async def test_f30_34_browser_crash_marks_session_failed_and_does_not_replay_side_effects() -> None:
    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    org = uuid4()
    worker = uuid4()
    session = await runtime.create_session(
        organization_id=org,
        worker_id=worker,
        run_id=uuid4(),
    )
    runtime.failure = PlaywrightError("Target page, context or browser has been closed")
    request = request_for(
        provider,
        operation="element.click",
        input={
            "sessionId": str(session.id),
            "locator": {"strategy": "role", "value": "button", "name": "Save"},
        },
        organization_id=org,
        worker_id=worker,
    )

    with pytest.raises(ExecutionProviderError) as captured:
        await provider.execute(
            request=request,
            configuration=request.resource.configuration,
            credential=None,
        )

    assert captured.value.error.code == "browser_crash"
    assert captured.value.error.retryable is False
    assert runtime.sessions[session.id].status == "failed"
    assert runtime.failed_sessions == [session.id]


def test_f30_32_recovery_policy_is_safe_for_retry_reobserve_and_crash() -> None:
    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    navigation = request_for(
        provider,
        operation="navigation.open",
        input={"url": "https://portal.example.test/home"},
    )
    click = request_for(
        provider,
        operation="element.click",
        input={
            "sessionId": str(uuid4()),
            "locator": {"strategy": "role", "value": "button", "name": "Save"},
        },
    )
    policy = RecoveryPolicy()

    retry = policy.plan(
        request=navigation,
        error=ExecutionError(
            code="navigation_timeout",
            retryable=True,
            provider="browser",
            operation=navigation.operation,
            correlation_id=navigation.correlation_id,
            safe_message="timeout",
        ),
        attempt=1,
    )
    assert retry.action == "retry_same_provider"

    reobserve = policy.plan(
        request=click,
        error=ExecutionError(
            code="stale_observation",
            retryable=False,
            provider="browser",
            operation=click.operation,
            correlation_id=click.correlation_id,
            safe_message="stale",
        ),
        attempt=1,
    )
    assert reobserve.action == "reobserve_replan"

    crash = policy.plan(
        request=click,
        error=ExecutionError(
            code="browser_crash",
            retryable=True,
            provider="browser",
            operation=click.operation,
            correlation_id=click.correlation_id,
            safe_message="crash",
        ),
        attempt=1,
    )
    assert crash.action == "escalate"


@pytest.mark.asyncio
async def test_f30_33_stale_state_recovery_reobserves_without_guessing_target() -> None:
    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    org = uuid4()
    worker = uuid4()
    session = await runtime.create_session(
        organization_id=org,
        worker_id=worker,
        run_id=uuid4(),
    )
    request = request_for(
        provider,
        operation="element.click",
        input={
            "sessionId": str(session.id),
            "locator": {
                "strategy": "observation_ref",
                "value": "e4",
                "observationId": "old-observation",
            },
        },
        organization_id=org,
        worker_id=worker,
    )
    detail = await provider.recover_execution(
        request=request,
        error=ExecutionError(
            code="stale_observation",
            retryable=False,
            provider="browser",
            operation=request.operation,
            correlation_id=request.correlation_id,
            safe_message="stale",
        ),
        result=None,
        configuration=request.resource.configuration,
        credential=None,
    )
    assert detail is not None
    assert "re-observed" in detail.lower()
    assert "replan" in detail.lower()


def test_f30_35_runtime_limits_are_bounded() -> None:
    assert _bounded_int(
        {"actionTimeoutMs": "999999"},
        "actionTimeoutMs",
        15_000,
        minimum=1_000,
        maximum=MAX_ACTION_TIMEOUT_MS,
    ) == MAX_ACTION_TIMEOUT_MS
    assert MAX_SESSION_TTL_SECONDS == 3600
    assert MAX_NAVIGATION_TIMEOUT_MS == 60_000
    assert MAX_ACTION_TIMEOUT_MS == 30_000
    assert MAX_DOWNLOAD_BYTES == 20 * 1024 * 1024
    assert MAX_VISIBLE_TEXT <= 24_000
    assert MAX_DOM_SNAPSHOT <= 32_000
    assert MAX_ARIA_SNAPSHOT <= 24_000
    assert MAX_ELEMENTS <= 300


@pytest.mark.asyncio
async def test_f30_36_private_network_and_metadata_egress_is_blocked() -> None:
    for url in (
        "http://127.0.0.1/admin",
        "http://10.1.2.3/internal",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/debug",
        "http://metadata.google.internal/computeMetadata/v1/",
    ):
        decision = await evaluate_browser_egress(url)
        assert decision.allowed is False

    explicit_dev = await evaluate_browser_egress(
        "http://127.0.0.1/test",
        allow_private_network=True,
    )
    assert explicit_dev.allowed is True


@pytest.mark.asyncio
async def test_f30_37_browser_events_are_normalized_and_verification_failure_is_emitted() -> None:
    expected_types = {
        "browser.session.created",
        "browser.navigation.started",
        "browser.navigation.completed",
        "browser.observation.created",
        "browser.action.started",
        "browser.action.completed",
        "browser.verification.failed",
        "browser.session.closed",
    }
    assert expected_types <= REALTIME_EVENT_TYPES

    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    request = request_for(
        provider,
        operation="navigation.open",
        input={
            "url": "https://portal.example.test/home",
            "verify": {"expectedText": "Missing"},
        },
    )
    result = await provider.execute(
        request=request,
        configuration=request.resource.configuration,
        credential=None,
    )
    output = result.output
    assert isinstance(output, dict)
    events = output["realtimeEvents"]
    assert isinstance(events, list)
    event_types = {str(event["type"]) for event in events if isinstance(event, dict)}
    assert {
        "browser.action.started",
        "browser.session.created",
        "browser.navigation.started",
        "browser.navigation.completed",
        "browser.observation.created",
        "browser.action.completed",
    } <= event_types

    verification = await provider.verify(
        request=request,
        result=result,
        configuration=request.resource.configuration,
        credential=None,
    )
    assert verification.verified is False
    event_types = {str(event["type"]) for event in events if isinstance(event, dict)}
    assert "browser.verification.failed" in event_types


@pytest.mark.asyncio
async def test_f30_38_browser_provider_conforms_to_universal_provider_surface() -> None:
    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    assert provider.manifest.provider == "browser"
    assert provider.manifest.kind == "browser"
    assert provider.manifest.version

    resources = await provider.discover_resources(
        configuration={
            "startUrl": "https://portal.example.test/home",
            "allowedOrigins": "https://portal.example.test",
        },
        credential=None,
    )
    assert len(resources) == 1
    resource = resources[0]
    assert resource.provider == "browser"
    capabilities = await provider.discover_capabilities(resource=resource)
    assert capabilities
    health = await provider.check_health(
        configuration=resource.configuration,
        credential=None,
    )
    assert health.state == "healthy"
    normalized = await provider.normalize_input(
        operation="navigation.open",
        input={"url": "https://portal.example.test/home"},
    )
    assert normalized["url"] == "https://portal.example.test/home"


@pytest.mark.asyncio
async def test_f30_39_cookie_and_localstorage_are_isolated_per_browser_context() -> None:
    browser = IsolationBrowser()
    runtime = IsolationRuntime(browser)
    organization_id = uuid4()

    first = await runtime.create_session(
        organization_id=organization_id,
        worker_id=uuid4(),
        run_id=uuid4(),
    )
    second = await runtime.create_session(
        organization_id=organization_id,
        worker_id=uuid4(),
        run_id=uuid4(),
    )

    first_context = cast(IsolationContext, runtime._sessions[first.id].context)
    second_context = cast(IsolationContext, runtime._sessions[second.id].context)
    assert first_context is not second_context

    first_context.cookie_jar["session"] = "worker-one-cookie"
    first_context.local_storage["token"] = "worker-one-localStorage"

    assert second_context.cookie_jar.get("session") is None
    assert second_context.local_storage.get("token") is None


@pytest.mark.asyncio
async def test_f30_39_tenant_file_and_destination_security_fail_closed() -> None:
    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    org = uuid4()
    owner = uuid4()
    session = await runtime.create_session(
        organization_id=org,
        worker_id=owner,
        run_id=uuid4(),
    )

    with pytest.raises(PermissionError, match="Cross-organization"):
        await runtime.resume(
            session.id,
            organization_id=uuid4(),
            worker_id=owner,
        )
    with pytest.raises(PermissionError, match="Cross-worker"):
        await runtime.resume(
            session.id,
            organization_id=org,
            worker_id=uuid4(),
        )

    with pytest.raises(ValueError, match="Unsupported browser input field"):
        await provider.normalize_input(
            operation="file.upload",
            input={
                "sessionId": str(session.id),
                "locator": {"strategy": "label", "value": "Upload"},
                "path": "/etc/passwd",
            },
        )

    policy = BrowserDomainPolicy.from_configuration(
        {"allowedOrigins": "https://portal.example.test"}
    )
    assert policy.permits("https://evil.example.test/").allowed is False


@pytest.mark.asyncio
async def test_browser_runtime_is_warmed_before_managed_runtime_traffic() -> None:
    runtime = HardeningRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    assert await provider.warmup() is True

    root = Path(__file__).resolve().parents[2]
    lifecycle = (root / "backend/app/bootstrap/lifecycle.py").read_text(encoding="utf-8")
    assert "browser_provider.warmup()" in lifecycle
    assert "runtime_execution_enabled" in lifecycle


def test_f30_40_vercel_auto_deployment_remains_disabled() -> None:
    root = Path(__file__).resolve().parents[2]
    vercel = (root / "vercel.json").read_text(encoding="utf-8")
    assert '"deploymentEnabled": false' in vercel
