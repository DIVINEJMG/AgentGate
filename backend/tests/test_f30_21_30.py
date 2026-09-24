from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from playwright.async_api import BrowserContext, Dialog, Frame, Page, Request, Route
from playwright.async_api import Error as PlaywrightError

from app.domain.actions.gateway import ActionProposal
from app.execution.authorization import (
    CredentialReference,
    ProviderPermissionSnapshot,
    UniversalActionRequest,
    build_authorization_snapshot,
)
from app.execution.browser.artifacts import (
    BrowserArtifactInput,
    BrowserArtifactReference,
)
from app.execution.browser.contracts import (
    BrowserDownload,
    BrowserElement,
    BrowserFrame,
    BrowserLocator,
    BrowserObservation,
    BrowserSession,
    BrowserVerificationProbe,
)
from app.execution.browser.policy import (
    BrowserDomainPolicy,
    BrowserNavigationBlocked,
)
from app.execution.browser.runtime import BrowserRuntime, _SessionHandle
from app.execution.browser.sensitive import is_sensitive_field_metadata
from app.execution.contracts import (
    ExecutionPreferences,
    ExecutionRequest,
    ProviderRuntimeContext,
    ResourceDescriptor,
)
from app.execution.lifecycle import ObserveActVerifyLifecycle
from app.execution.provider_executor import UniversalProviderExecutor
from app.execution.providers.browser import PlaywrightBrowserProvider
from app.execution.providers.registry import ProviderRegistry
from app.execution.redaction import redact_sensitive_structure, redact_url


class MemoryArtifactStore:
    def __init__(self) -> None:
        self.inputs: dict[UUID, BrowserArtifactInput] = {}
        self.stored: list[dict[str, object]] = []

    async def store(
        self,
        *,
        organization_id,
        worker_id,
        run_id,
        session_id,
        correlation_id,
        operation,
        kind,
        name,
        media_type,
        content,
        source_url=None,
    ) -> BrowserArtifactReference:
        checksum = hashlib.sha256(content).hexdigest()
        artifact = BrowserArtifactReference(
            id=uuid4(),
            kind=kind,
            media_type=media_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            name=name,
            source_url=source_url,
        )
        self.stored.append(
            {
                "organization_id": organization_id,
                "worker_id": worker_id,
                "run_id": run_id,
                "session_id": session_id,
                "correlation_id": correlation_id,
                "operation": operation,
                "kind": kind,
                "name": name,
                "media_type": media_type,
                "content": content,
                "source_url": source_url,
                "reference": artifact,
            }
        )
        return artifact

    async def load_input(
        self,
        *,
        organization_id,
        worker_id,
        run_id,
        artifact_id,
    ) -> BrowserArtifactInput:
        del organization_id, worker_id, run_id
        try:
            return self.inputs[artifact_id]
        except KeyError as error:
            raise LookupError("Authorized browser upload artifact was not found.") from error


class EvidenceRuntime:
    def __init__(self) -> None:
        self.sessions: dict[UUID, BrowserSession] = {}
        self.state = 0
        self.upload_received: bytes | None = None
        self.upload_name: str | None = None
        self.verification_probe = BrowserVerificationProbe(
            verified=True,
            details={"expectedText": True},
        )

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
            current_url="https://portal.example.test/start",
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
        return session

    async def close(self, session_id: UUID) -> BrowserSession:
        return self.sessions.pop(session_id)

    async def expire(self, session_id: UUID) -> BrowserSession:
        return self.sessions.pop(session_id)

    async def terminate(self, session_id: UUID) -> BrowserSession:
        return self.sessions.pop(session_id)

    async def fail(self, session_id: UUID) -> BrowserSession:
        session = self.sessions.pop(session_id)
        return replace(session, status="failed")

    def observation(self, session_id: UUID) -> BrowserObservation:
        return BrowserObservation(
            id=f"obs_{uuid4().hex}",
            session_id=session_id,
            url=f"https://portal.example.test/state/{self.state}",
            title=f"Portal {self.state}",
            visible_text=f"State {self.state}",
            aria_snapshot=f'- heading "State {self.state}"',
            dom_snapshot=f"<main>State {self.state}</main>",
            elements=(
                BrowserElement(
                    ref="e1",
                    tag="button",
                    role="button",
                    name="Save",
                    text="Save",
                    element_type="button",
                    value=None,
                    checked=None,
                    selected=None,
                    disabled=False,
                    href=None,
                ),
            ),
            forms=(),
            links=(),
            buttons=("e1",),
            inputs=(),
            frames=(
                BrowserFrame(
                    name=None,
                    url=f"https://portal.example.test/state/{self.state}",
                    origin="https://portal.example.test",
                    is_main=True,
                ),
            ),
            page_state={"loadState": "complete", "state": self.state},
            observed_at=datetime.now(UTC),
        )

    async def observe(self, session_id, *, organization_id, worker_id):
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        return self.observation(session_id)

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
        del operation, url, locator, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.state += 1
        return self.observation(session_id)

    async def interact(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        operation,
        locator=None,
        value=None,
        dialog_action=None,
        prompt_text=None,
        timeout_ms=15000,
    ):
        del operation, locator, value, dialog_action, prompt_text, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.state += 1
        return self.observation(session_id)

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
        self.state += 1
        return self.observation(session_id)

    async def submit_form(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        form_ref=None,
        submit_locator=None,
        dialog_action=None,
        prompt_text=None,
        timeout_ms=15000,
    ):
        del form_ref, submit_locator, dialog_action, prompt_text, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.state += 1
        return self.observation(session_id)

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
        self.state += 1
        return self.observation(session_id)

    async def screenshot(self, session_id, *, organization_id, worker_id):
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        return b"PNG-f30-evidence"

    async def upload_file(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        locator,
        name,
        media_type,
        content,
        dialog_action=None,
        prompt_text=None,
        timeout_ms=15000,
    ):
        del locator, media_type, dialog_action, prompt_text, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.upload_received = content
        self.upload_name = name
        self.state += 1
        return self.observation(session_id)

    async def download_file(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        locator,
        timeout_ms=30000,
        max_bytes=20 * 1024 * 1024,
    ):
        del locator, timeout_ms, max_bytes
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.state += 1
        return (
            BrowserDownload(
                name="report.txt",
                source_url="https://portal.example.test/report.txt",
                content=b"downloaded-report",
                media_type="text/plain",
            ),
            self.observation(session_id),
        )

    async def verify_browser_state(
        self,
        session_id,
        *,
        organization_id,
        worker_id,
        expectation,
        result_output,
    ):
        del expectation, result_output
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        return self.verification_probe


def browser_resource(
    provider: PlaywrightBrowserProvider,
    *,
    enable_file_transfer: bool = True,
) -> ResourceDescriptor:
    configuration = {
        "allowedOrigins": "https://portal.example.test,https://frames.example.test",
        "enableFileTransfer": "true" if enable_file_transfer else "false",
    }
    return ResourceDescriptor(
        id=f"integration:{uuid4()}",
        provider="browser",
        resource_type="origin",
        external_id="origin:https://portal.example.test",
        display_name="Approved Portal",
        metadata={"origin": "https://portal.example.test"},
        health="healthy",
        available_capabilities=tuple(item.scope for item in provider.manifest.capabilities),
        web_url="https://portal.example.test/start",
        configuration=configuration,
    )


def request_for(
    provider: PlaywrightBrowserProvider,
    runtime: EvidenceRuntime,
    *,
    operation: str,
    payload: dict[str, object],
    resource_value: ResourceDescriptor | None = None,
) -> ExecutionRequest:
    capability = next(
        item for item in provider.manifest.capabilities if item.operation == operation
    )
    session_id = UUID(str(payload["sessionId"]))
    session = runtime.sessions[session_id]
    return ExecutionRequest(
        organization_id=session.organization_id,
        worker_id=session.worker_id,
        agent_id=uuid4(),
        job_id=uuid4(),
        work_item_id=uuid4(),
        run_id=session.run_id,
        capability=capability,
        resource=resource_value or browser_resource(provider),
        operation=operation,
        input=payload,
        correlation_id=f"f30-21-30-{uuid4()}",
        idempotency_key=f"idem-{uuid4()}",
        execution_preferences=ExecutionPreferences(
            preferred_provider_kinds=("browser",),
            allow_fallback=False,
        ),
    )


async def opened(
    provider: PlaywrightBrowserProvider,
    runtime: EvidenceRuntime,
    *,
    operation: str,
    extra: dict[str, object],
    resource_value: ResourceDescriptor | None = None,
) -> ExecutionRequest:
    organization_id = uuid4()
    worker_id = uuid4()
    session = await runtime.create_session(
        organization_id=organization_id,
        worker_id=worker_id,
        run_id=uuid4(),
    )
    normalized = await provider.normalize_input(
        operation=operation,
        input={"sessionId": str(session.id), **extra},
    )
    return request_for(
        provider,
        runtime,
        operation=operation,
        payload=normalized,
        resource_value=resource_value,
    )


def test_f30_21_sensitive_fields_and_nested_payloads_are_redacted() -> None:
    assert is_sensitive_field_metadata(element_type="password")
    assert is_sensitive_field_metadata(autocomplete="one-time-code")
    assert is_sensitive_field_metadata(label="API Token")

    value = {
        "fields": [
            {
                "locator": {"strategy": "label", "value": "Password"},
                "value": "super-secret",
            },
            {
                "locator": {"strategy": "label", "value": "Display name"},
                "value": "Divine",
            },
        ],
        "accessToken": "token-value",
    }
    redacted = redact_sensitive_structure(value)
    assert isinstance(redacted, dict)
    fields = redacted["fields"]
    assert isinstance(fields, list)
    assert fields[0]["value"] == "[REDACTED]"  # type: ignore[index]
    assert fields[1]["value"] == "Divine"  # type: ignore[index]
    assert redacted["accessToken"] == "[REDACTED]"
    assert "super-secret" not in repr(redacted)
    assert "token-value" not in repr(redacted)
    safe_url = redact_url("https://portal.example.test/callback?token=abc123&view=full")
    assert "abc123" not in safe_url
    assert "view=full" in safe_url


@pytest.mark.asyncio
async def test_f30_22_23_sensitive_action_stores_screenshot_refs_and_action_evidence() -> None:
    runtime = EvidenceRuntime()
    store = MemoryArtifactStore()
    provider = PlaywrightBrowserProvider(runtime, artifact_store=store)
    request = await opened(
        provider,
        runtime,
        operation="element.click",
        extra={"locator": {"strategy": "role", "value": "button", "name": "Save"}},
    )

    result = await provider.execute(
        request=request,
        configuration=request.resource.configuration,
        credential=None,
    )
    verification = await provider.verify(
        request=request,
        result=result,
        configuration=request.resource.configuration,
        credential=None,
    )

    assert verification.verified is True
    assert [item["kind"] for item in store.stored] == [
        "browser_before_action",
        "browser_after_action",
    ]
    assert len(result.artifacts) == 2
    output = result.output
    assert isinstance(output, dict)
    evidence = output["actionEvidence"]
    assert isinstance(evidence, dict)
    assert evidence["sessionId"]
    assert evidence["pageUrl"]
    assert evidence["capability"] == "browser.element.click"
    assert evidence["elementReference"] == "button"
    assert evidence["operation"] == "element.click"
    assert evidence["correlationId"] == request.correlation_id
    assert evidence["stateChanged"] is True
    assert all("content" not in item for item in evidence["artifacts"])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_f30_24_download_is_persisted_as_artifact_without_path_or_blob_output() -> None:
    runtime = EvidenceRuntime()
    store = MemoryArtifactStore()
    provider = PlaywrightBrowserProvider(runtime, artifact_store=store)
    request = await opened(
        provider,
        runtime,
        operation="file.download",
        extra={"locator": {"strategy": "text", "value": "Download report"}},
    )

    result = await provider.execute(
        request=request,
        configuration=request.resource.configuration,
        credential=None,
    )

    download_store = next(item for item in store.stored if item["kind"] == "browser_download")
    assert download_store["content"] == b"downloaded-report"
    output = result.output
    assert isinstance(output, dict)
    reference = output["downloadArtifact"]
    assert isinstance(reference, dict)
    assert reference["sourceUrl"] == "https://portal.example.test/report.txt"
    assert reference["mediaType"] == "text/plain"
    assert reference["checksumSha256"] == hashlib.sha256(b"downloaded-report").hexdigest()
    rendered = repr(output)
    assert "downloaded-report" not in rendered
    assert "/tmp/" not in rendered


@pytest.mark.asyncio
async def test_f30_25_upload_uses_authorized_artifact_bytes_not_host_path() -> None:
    runtime = EvidenceRuntime()
    store = MemoryArtifactStore()
    provider = PlaywrightBrowserProvider(runtime, artifact_store=store)
    artifact_id = uuid4()
    store.inputs[artifact_id] = BrowserArtifactInput(
        id=artifact_id,
        name="input.txt",
        media_type="text/plain",
        content=b"authorized-input",
        checksum_sha256=hashlib.sha256(b"authorized-input").hexdigest(),
    )
    request = await opened(
        provider,
        runtime,
        operation="file.upload",
        extra={
            "artifactId": str(artifact_id),
            "locator": {"strategy": "label", "value": "Attach file"},
        },
    )

    result = await provider.execute(
        request=request,
        configuration=request.resource.configuration,
        credential=None,
    )

    assert runtime.upload_received == b"authorized-input"
    assert runtime.upload_name == "input.txt"
    assert "path" not in request.input
    assert "authorized-input" not in repr(result.output)


@pytest.mark.asyncio
async def test_f30_25_file_transfer_permissions_fail_closed_until_enabled() -> None:
    runtime = EvidenceRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    disabled = browser_resource(provider, enable_file_transfer=False)
    enabled = browser_resource(provider, enable_file_transfer=True)

    disabled_snapshot = await provider.discover_permissions(
        resource=disabled,
        configuration=disabled.configuration,
        credential=None,
        credential_reference=None,
    )
    enabled_snapshot = await provider.discover_permissions(
        resource=enabled,
        configuration=enabled.configuration,
        credential=None,
        credential_reference=None,
    )

    assert "browser.file.upload" not in disabled_snapshot.capability_scopes
    assert "browser.file.download" not in disabled_snapshot.capability_scopes
    assert "browser.file.upload" in enabled_snapshot.capability_scopes
    assert "browser.file.download" in enabled_snapshot.capability_scopes


class FakePage:
    def __init__(self, url: str, frames: list[object] | None = None) -> None:
        self.url = url
        self.frames = frames or []
        self.handlers: dict[str, object] = {}
        self.closed = False

    def on(self, event: str, callback) -> None:
        self.handlers[event] = callback

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True
        callback = self.handlers.get("close")
        if callable(callback):
            callback(self)

    async def wait_for_timeout(self, timeout: float) -> None:
        del timeout


class FakeFrame:
    def __init__(self, *, name: str, url: str) -> None:
        self.name = name
        self.url = url


def runtime_handle(
    *,
    page: FakePage,
    policy: BrowserDomainPolicy,
) -> _SessionHandle:
    now = datetime.now(UTC)
    session = BrowserSession(
        id=uuid4(),
        organization_id=uuid4(),
        worker_id=uuid4(),
        run_id=uuid4(),
        browser_context_id="ctx-test",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        status="active",
    )
    return _SessionHandle(
        session=session,
        context=cast(BrowserContext, object()),
        page=cast(Page, page),
        navigation_policy=policy,
    )


def test_f30_26_popup_pages_stay_tracked_inside_same_session_handle() -> None:
    runtime = BrowserRuntime()
    policy = BrowserDomainPolicy.from_configuration(
        {"allowedOrigins": "https://portal.example.test"}
    )
    first = FakePage("https://portal.example.test/main")
    popup = FakePage("https://portal.example.test/popup")
    handle = runtime_handle(page=first, policy=policy)

    runtime._register_page(handle, cast(Page, first))
    runtime._register_page(handle, cast(Page, popup))

    assert list(handle.pages) == ["p1", "p2"]
    assert handle.page is cast(Page, popup)
    assert "dialog" in first.handlers
    assert "dialog" in popup.handlers


class PreFramePopupRequest:
    url = "https://evil.example.test/popup"

    def is_navigation_request(self) -> bool:
        return True

    @property
    def frame(self):
        raise PlaywrightError("popup frame is not available yet")


class RecordingRoute:
    def __init__(self) -> None:
        self.aborted = False
        self.abort_code: str | None = None

    async def abort(self, error_code: str | None = None) -> None:
        self.aborted = True
        self.abort_code = error_code


@pytest.mark.asyncio
async def test_f30_26_denied_pre_frame_popup_is_surfaced_and_cleaned() -> None:
    runtime = BrowserRuntime()
    policy = BrowserDomainPolicy.from_configuration(
        {"allowedOrigins": "https://portal.example.test"}
    )
    first = FakePage("https://portal.example.test/main")
    popup = FakePage("about:blank")
    handle = runtime_handle(page=first, policy=policy)
    runtime._register_page(handle, cast(Page, first))

    route = RecordingRoute()
    await runtime._route_request(
        handle,
        cast(Route, route),
        cast(Request, PreFramePopupRequest()),
    )

    assert route.aborted is True
    assert route.abort_code == "blockedbyclient"
    assert handle.blocked_navigation is not None
    assert handle.blocked_navigation.target_origin == "https://evil.example.test"
    assert handle.blocked_popup_initial_navigation is True

    runtime._register_page(handle, cast(Page, popup))
    with pytest.raises(BrowserNavigationBlocked):
        await runtime._raise_if_blocked(handle)

    assert popup.closed is True
    assert list(handle.pages) == ["p1"]
    assert handle.page is cast(Page, first)
    assert handle.blocked_popup_initial_navigation is False


def test_f30_27_cross_origin_frame_interaction_requires_authorized_origin() -> None:
    runtime = BrowserRuntime()
    allowed_frame = FakeFrame(
        name="approved",
        url="https://frames.example.test/panel",
    )
    denied_frame = FakeFrame(
        name="denied",
        url="https://evil.example.test/panel",
    )
    page = FakePage(
        "https://portal.example.test/main",
        frames=[allowed_frame, denied_frame],
    )
    policy = BrowserDomainPolicy.from_configuration(
        {"allowedOrigins": ("https://portal.example.test,https://frames.example.test")}
    )
    handle = runtime_handle(page=page, policy=policy)

    selected = runtime._frame_for_locator(
        handle,
        BrowserLocator(
            strategy="text",
            value="Save",
            frame_name="approved",
            frame_origin="https://frames.example.test",
        ),
    )
    assert selected is cast(Frame, allowed_frame)

    with pytest.raises(BrowserNavigationBlocked):
        runtime._frame_for_locator(
            handle,
            BrowserLocator(
                strategy="text",
                value="Save",
                frame_name="denied",
                frame_origin="https://evil.example.test",
            ),
        )


class FakeDialog:
    def __init__(self) -> None:
        self.type = "confirm"
        self.message = "Delete account?"
        self.default_value = ""
        self.accepted = False
        self.dismissed = False
        self.prompt_text: str | None = None

    async def accept(self, prompt_text: str = "") -> None:
        self.accepted = True
        self.prompt_text = prompt_text

    async def dismiss(self) -> None:
        self.dismissed = True


@pytest.mark.asyncio
async def test_f30_28_dialogs_default_to_dismiss_and_accept_is_explicit() -> None:
    runtime = BrowserRuntime()
    page = FakePage("https://portal.example.test/main")
    handle = runtime_handle(
        page=page,
        policy=BrowserDomainPolicy.from_configuration(
            {"allowedOrigins": "https://portal.example.test"}
        ),
    )
    dismissed = FakeDialog()
    await runtime._handle_dialog(handle, cast(Dialog, dismissed))
    assert dismissed.dismissed is True
    assert dismissed.accepted is False
    assert handle.dialog_events[-1]["decision"] == "dismissed"

    accepted = FakeDialog()
    runtime._set_dialog_policy(handle, action="accept", prompt_text="approved")
    await runtime._handle_dialog(handle, cast(Dialog, accepted))
    assert accepted.accepted is True
    assert accepted.prompt_text == "approved"
    assert handle.dialog_events[-1]["decision"] == "accepted"


@pytest.mark.asyncio
async def test_f30_28_dialog_accept_is_restricted_to_high_risk_operations() -> None:
    provider = PlaywrightBrowserProvider(EvidenceRuntime())
    session_id = str(uuid4())

    with pytest.raises(ValueError, match="restricted"):
        await provider.normalize_input(
            operation="element.click",
            input={
                "sessionId": session_id,
                "locator": {"strategy": "role", "value": "button"},
                "dialog": {"action": "accept"},
            },
        )

    normalized = await provider.normalize_input(
        operation="form.submit",
        input={
            "sessionId": session_id,
            "formRef": "f1",
            "dialog": {"action": "accept"},
        },
    )
    assert normalized["dialog"] == {"action": "accept"}


@pytest.mark.asyncio
async def test_f30_29_explicit_verification_failure_captures_evidence() -> None:
    runtime = EvidenceRuntime()
    runtime.verification_probe = BrowserVerificationProbe(
        verified=False,
        details={"expectedText": False},
    )
    store = MemoryArtifactStore()
    provider = PlaywrightBrowserProvider(runtime, artifact_store=store)
    request = await opened(
        provider,
        runtime,
        operation="element.click",
        extra={
            "locator": {"strategy": "role", "value": "button", "name": "Save"},
            "verify": {"expectedText": "Saved"},
        },
    )
    result = await provider.execute(
        request=request,
        configuration=request.resource.configuration,
        credential=None,
    )

    verification = await provider.verify(
        request=request,
        result=result,
        configuration=request.resource.configuration,
        credential=None,
    )

    assert verification.verified is False
    assert "verificationEvidenceArtifact" in verification.details
    assert any(item["kind"] == "browser_verification_failure" for item in store.stored)


@pytest.mark.asyncio
async def test_f30_30_browser_reuses_existing_observe_act_verify_lifecycle() -> None:
    runtime = EvidenceRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    request = await opened(
        provider,
        runtime,
        operation="element.click",
        extra={"locator": {"strategy": "role", "value": "button", "name": "Save"}},
    )
    lifecycle = ObserveActVerifyLifecycle()

    outcome = await lifecycle.run(
        request=request,
        execute=lambda: provider.execute(
            request=request,
            configuration=request.resource.configuration,
            credential=None,
        ),
        verify=lambda result: provider.verify(
            request=request,
            result=result,
            configuration=request.resource.configuration,
            credential=None,
        ),
    )

    assert outcome.error is None
    assert outcome.verification is not None
    assert outcome.verification.verified is True
    stages = [checkpoint.stage for checkpoint in outcome.checkpoints]
    assert stages == [
        "observe",
        "locate",
        "plan",
        "policy_check",
        "act",
        "verify",
        "completed",
    ]


class RecordingContextLoader:
    def __init__(self, context: ProviderRuntimeContext) -> None:
        self.context = context
        self.recorded = 0

    async def load(self, proposal: ActionProposal) -> ProviderRuntimeContext:
        del proposal
        return self.context

    async def record_execution_evidence(
        self,
        *,
        request,
        result,
        verification,
        snapshot,
    ) -> None:
        del request, result, verification, snapshot
        self.recorded += 1


@pytest.mark.asyncio
async def test_f30_23_30_universal_executor_records_evidence_after_lifecycle() -> None:
    runtime = EvidenceRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    request = await opened(
        provider,
        runtime,
        operation="element.click",
        extra={"locator": {"strategy": "role", "value": "button", "name": "Save"}},
    )
    context = ProviderRuntimeContext(
        configuration=request.resource.configuration,
        credential=None,
        resource=request.resource,
    )
    loader = RecordingContextLoader(context)
    executor = UniversalProviderExecutor(
        registry=ProviderRegistry((provider,)),
        context_loader=loader,
    )
    permissions = ProviderPermissionSnapshot(
        provider="browser",
        resource_id=request.resource.id,
        capability_scopes=(request.capability.scope,),
        credential=CredentialReference(strategy="none", reference=None),
        checked_at=datetime.now(UTC),
        adapter="browser",
        adapter_version=provider.manifest.version,
    )
    universal = UniversalActionRequest(
        execution=request,
        provider_permissions=permissions,
    )
    snapshot = build_authorization_snapshot(
        request=universal,
        risk=request.capability.risk,
        policy_outcome="ALLOW",
        policy_reason="test",
        adapter="browser",
        adapter_version=provider.manifest.version,
    )

    result = await executor.execute_request(universal, snapshot)

    result_data = result.data
    assert isinstance(result_data, dict)
    verification_data = result_data["verification"]
    assert isinstance(verification_data, dict)
    assert verification_data["verified"] is True
    assert loader.recorded == 1
    lifecycle = result_data["lifecycle"]
    assert isinstance(lifecycle, list)
    assert all(isinstance(item, dict) for item in lifecycle)
    assert [item["stage"] for item in lifecycle if isinstance(item, dict)][-2:] == [
        "verify",
        "completed",
    ]


def test_f30_21_30_vercel_auto_deploy_remains_disabled() -> None:
    root = Path(__file__).resolve().parents[2]
    assert '"deploymentEnabled": false' in (root / "vercel.json").read_text(encoding="utf-8")
