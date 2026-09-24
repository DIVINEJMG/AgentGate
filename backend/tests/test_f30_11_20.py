from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.domain.actions.gateway import ActionGateway, AuthorizationDecision
from app.domain.identity.principals import AgentPrincipal
from app.domain.integrations.contracts import IntegrationExecutionResult
from app.execution.authorization import (
    CredentialReference,
    ExecutionAuthorizationSnapshot,
    ProviderPermissionSnapshot,
    UniversalActionRequest,
    action_fingerprint,
)
from app.execution.browser.contracts import (
    BrowserDownload,
    BrowserElement,
    BrowserFrame,
    BrowserObservation,
    BrowserSession,
    BrowserVerificationProbe,
)
from app.execution.browser.credentials import (
    BrowserAuthenticationFailure,
    BrowserCredentialBundle,
)
from app.execution.browser.policy import BrowserDomainPolicy
from app.execution.contracts import (
    ExecutionPreferences,
    ExecutionProviderError,
    ExecutionRequest,
    ResourceDescriptor,
)
from app.execution.providers.browser import (
    FILE_TRANSFER_SCOPES,
    PlaywrightBrowserProvider,
)


class FakeGovernedRuntime:
    def __init__(self) -> None:
        self.sessions: dict[UUID, BrowserSession] = {}
        self.operations: list[str] = []
        self.last_credential_values: tuple[str, ...] = ()

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
            current_url="https://portal.example.test/login",
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
            url="https://portal.example.test/account",
            title="Portal",
            visible_text=f"Account ready {self.operations!r}",
            aria_snapshot=f'- heading "Account" {self.operations!r}',
            dom_snapshot=f"<main>Account ready {self.operations!r}</main>",
            elements=(
                BrowserElement(
                    ref="e1",
                    tag="button",
                    role="button",
                    name="Submit",
                    text="Submit",
                    element_type="submit",
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
                    url="https://portal.example.test/account",
                    origin="https://portal.example.test",
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
        del url, locator, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.operations.append(operation)
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
        del locator, value, dialog_action, prompt_text, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.operations.append(operation)
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
        self.operations.append("form.fill")
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
        self.operations.append("form.submit")
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
        self.last_credential_values = credentials.sensitive_values()
        self.operations.append("auth.login")
        return self.observation(session_id)

    async def screenshot(self, session_id, *, organization_id, worker_id):
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        return b"fake-png"

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
        del locator, name, media_type, content, dialog_action, prompt_text, timeout_ms
        await self.resume(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        self.operations.append("file.upload")
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
        self.operations.append("file.download")
        return (
            BrowserDownload(
                name="report.txt",
                source_url="https://portal.example.test/report.txt",
                content=b"report",
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
        return BrowserVerificationProbe(verified=True, details={"fake": True})


class ApprovalGuard:
    async def evaluate(self, *, principal, proposal):
        del principal
        if proposal.risk in {"high", "critical"}:
            return AuthorizationDecision(
                "REQUIRE_APPROVAL",
                "High-risk browser action requires approval.",
            )
        return AuthorizationDecision("ALLOW", "Low-risk action allowed.")


class BrowserGatewayExecutor:
    def __init__(self, provider: PlaywrightBrowserProvider) -> None:
        self.provider = provider
        self.snapshot: ExecutionAuthorizationSnapshot | None = None

    async def execute_request(self, request, snapshot):
        self.snapshot = snapshot
        result = await self.provider.execute(
            request=request.execution,
            configuration=request.execution.resource.configuration,
            credential=None,
        )
        return IntegrationExecutionResult(
            provider_operation=result.operation,
            summary="executed",
            data={
                "output": result.output,
                "authorization": snapshot.as_dict(),
            },
        )

    async def prepare(self, proposal):
        raise AssertionError("not used")

    async def execute(self, proposal):
        raise AssertionError("legacy execution must not be used")


def resource(
    provider: PlaywrightBrowserProvider,
    *,
    credential_scope: bool = True,
) -> ResourceDescriptor:
    capabilities = tuple(
        item.scope
        for item in provider.manifest.capabilities
        if item.scope not in FILE_TRANSFER_SCOPES
        and (credential_scope or not item.requires_credential)
    )
    return ResourceDescriptor(
        id=f"integration:{uuid4()}",
        provider="browser",
        resource_type="origin",
        external_id="origin:https://portal.example.test",
        display_name="Approved Portal",
        metadata={"origin": "https://portal.example.test"},
        health="healthy",
        available_capabilities=capabilities,
        web_url="https://portal.example.test/login",
        configuration={
            "allowedOrigins": ("https://portal.example.test,https://accounts.example.test"),
            "deniedOrigins": "https://evil.example.test",
            "allowedPaths": "/login,/account,/submit",
        },
    )


def request_for(
    provider: PlaywrightBrowserProvider,
    *,
    operation: str,
    payload: dict[str, object],
    organization_id: UUID | None = None,
    worker_id: UUID | None = None,
    resource_value: ResourceDescriptor | None = None,
) -> ExecutionRequest:
    capability = next(
        item for item in provider.manifest.capabilities if item.operation == operation
    )
    selected_resource = resource_value or resource(provider)
    return ExecutionRequest(
        organization_id=organization_id or uuid4(),
        worker_id=worker_id or uuid4(),
        agent_id=uuid4(),
        job_id=uuid4(),
        work_item_id=uuid4(),
        run_id=uuid4(),
        capability=capability,
        resource=selected_resource,
        operation=operation,
        input=payload,
        correlation_id=f"f30-11-20-{uuid4()}",
        idempotency_key=f"idem-{uuid4()}",
        execution_preferences=ExecutionPreferences(
            preferred_provider_kinds=("browser",),
            allow_fallback=False,
        ),
    )


def principal(request: ExecutionRequest, *, risk: str = "low") -> AgentPrincipal:
    return AgentPrincipal(
        agent_id=request.agent_id,
        organization_id=request.organization_id,
        credential_fingerprint="agent-fingerprint",
        capabilities=frozenset({request.capability.scope}),
        risk_level=risk,  # type: ignore[arg-type]
        policy_context=(),
    )


def universal(
    request: ExecutionRequest,
    *,
    approved: bool = False,
    credential_reference: str | None = None,
) -> UniversalActionRequest:
    permissions = ProviderPermissionSnapshot(
        provider="browser",
        resource_id=request.resource.id,
        capability_scopes=(request.capability.scope,),
        credential=(
            CredentialReference(
                strategy="secret_reference",
                reference=credential_reference,
            )
            if credential_reference is not None
            else CredentialReference(strategy="none", reference=None)
        ),
        checked_at=datetime.now(UTC),
        adapter="browser",
        adapter_version="1.0.0",
    )
    pending = UniversalActionRequest(
        execution=request,
        provider_permissions=permissions,
    )
    if not approved:
        return pending
    return UniversalActionRequest(
        execution=request,
        provider_permissions=permissions,
        approval_id=str(uuid4()),
        approval_status="approved",
        approval_action_fingerprint=action_fingerprint(pending),
    )


def test_f30_12_browser_capability_catalog_has_governance_metadata() -> None:
    provider = PlaywrightBrowserProvider(FakeGovernedRuntime())
    by_scope = {item.scope: item for item in provider.manifest.capabilities}

    for scope in (
        "browser.page.read",
        "browser.navigation.open",
        "browser.element.click",
        "browser.form.fill",
        "browser.form.submit",
        "browser.file.upload",
        "browser.file.download",
        "browser.auth.login",
    ):
        capability = by_scope[scope]
        assert capability.mode in {"read", "write", "action"}
        assert capability.risk in {"low", "medium", "high", "critical"}
        assert capability.approval_recommendation in {
            "none",
            "recommended",
            "required",
        }
        assert capability.input_schema["type"] == "object"
        assert capability.output_schema["type"] == "object"

    assert by_scope["browser.form.fill"].side_effect is False
    assert by_scope["browser.form.submit"].side_effect is True
    assert by_scope["browser.form.submit"].risk == "high"
    assert by_scope["browser.auth.login"].requires_credential is True


@pytest.mark.asyncio
async def test_f30_13_browser_resource_is_origin_scoped_and_file_transfer_is_off() -> None:
    provider = PlaywrightBrowserProvider(FakeGovernedRuntime())
    discovered = await provider.discover_resources(
        configuration={
            "startUrl": "https://portal.example.test/login",
            "allowedOrigins": "https://portal.example.test",
        },
        credential=None,
    )

    assert len(discovered) == 1
    found = discovered[0]
    assert found.resource_type == "origin"
    assert found.external_id == "origin:https://portal.example.test"
    assert found.metadata["origin"] == "https://portal.example.test"
    assert FILE_TRANSFER_SCOPES.isdisjoint(found.available_capabilities)


@pytest.mark.asyncio
async def test_f30_13_browser_resource_without_authorized_origin_fails_closed() -> None:
    provider = PlaywrightBrowserProvider(FakeGovernedRuntime())

    with pytest.raises(ValueError, match="startUrl|allowedOrigins"):
        await provider.discover_resources(configuration={}, credential=None)


def test_f30_14_domain_allow_deny_and_path_policy_fails_closed() -> None:
    policy = BrowserDomainPolicy.from_configuration(
        {
            "allowedOrigins": ("https://portal.example.test,https://accounts.example.test"),
            "deniedOrigins": "https://evil.example.test",
            "allowedPaths": "/login,/account",
            "deniedPaths": "/account/delete",
        }
    )

    assert policy.permits("https://portal.example.test/account").allowed is True
    assert policy.permits("https://portal.example.test/admin").allowed is False
    assert policy.permits("https://portal.example.test/account/delete").allowed is False
    assert policy.permits("https://evil.example.test/login").allowed is False
    assert policy.permits("https://unknown.example.test/login").allowed is False


def test_f30_15_cross_origin_transition_is_explicitly_evaluated() -> None:
    policy = BrowserDomainPolicy.from_configuration(
        {"allowedOrigins": ("https://portal.example.test,https://accounts.example.test")}
    )

    approved = policy.permits(
        "https://accounts.example.test/login",
        source_url="https://portal.example.test/account",
    )
    blocked = policy.permits(
        "https://elsewhere.example.test/login",
        source_url="https://portal.example.test/account",
    )

    assert approved.allowed is True
    assert approved.cross_origin is True
    assert blocked.allowed is False
    assert blocked.cross_origin is True


@pytest.mark.asyncio
async def test_f30_16_17_high_risk_submit_pauses_and_resumes_exact_action() -> None:
    runtime = FakeGovernedRuntime()
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
        operation="form.submit",
        payload={"sessionId": str(session.id), "formRef": "f1"},
        organization_id=organization_id,
        worker_id=worker_id,
    )
    pending = universal(request)
    executor = BrowserGatewayExecutor(provider)
    gateway = ActionGateway((ApprovalGuard(),), executor)

    with pytest.raises(PermissionError, match="Human approval required"):
        await gateway.execute_request(
            principal=principal(request, risk="low"),
            request=pending,
        )

    assert session.id in runtime.sessions
    assert runtime.operations == []

    approved = universal(request, approved=True)
    result = await gateway.execute_request(
        principal=principal(request, risk="low"),
        request=approved,
    )

    assert result.summary == "executed"
    assert runtime.operations == ["form.submit"]
    assert executor.snapshot is not None
    assert executor.snapshot.risk == "high"
    assert executor.snapshot.approval_required is True
    assert executor.snapshot.action_fingerprint == action_fingerprint(approved)


@pytest.mark.asyncio
async def test_f30_17_approval_cannot_be_reused_for_modified_action() -> None:
    runtime = FakeGovernedRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    organization_id = uuid4()
    worker_id = uuid4()
    session = await runtime.create_session(
        organization_id=organization_id,
        worker_id=worker_id,
        run_id=uuid4(),
    )
    original = request_for(
        provider,
        operation="form.submit",
        payload={"sessionId": str(session.id), "formRef": "f1"},
        organization_id=organization_id,
        worker_id=worker_id,
    )
    approved = universal(original, approved=True)
    changed = ExecutionRequest(
        organization_id=original.organization_id,
        worker_id=original.worker_id,
        agent_id=original.agent_id,
        job_id=original.job_id,
        work_item_id=original.work_item_id,
        run_id=original.run_id,
        capability=original.capability,
        resource=original.resource,
        operation=original.operation,
        input={
            "sessionId": str(session.id),
            "submitLocator": {"strategy": "role", "value": "button", "name": "Other"},
        },
        correlation_id=original.correlation_id,
        idempotency_key=original.idempotency_key,
        execution_preferences=original.execution_preferences,
    )
    tampered = UniversalActionRequest(
        execution=changed,
        provider_permissions=approved.provider_permissions,
        approval_id=approved.approval_id,
        approval_status=approved.approval_status,
        approval_action_fingerprint=approved.approval_action_fingerprint,
    )
    gateway = ActionGateway((ApprovalGuard(),), BrowserGatewayExecutor(provider))

    with pytest.raises(PermissionError, match="exact governed action"):
        await gateway.execute_request(
            principal=principal(changed),
            request=tampered,
        )


@pytest.mark.asyncio
async def test_f30_18_authorization_snapshot_records_browser_provenance() -> None:
    provider = PlaywrightBrowserProvider(FakeGovernedRuntime())
    request = request_for(
        provider,
        operation="auth.login",
        payload={
            "sessionId": str(uuid4()),
            "bindings": [
                {
                    "locator": {"strategy": "label", "value": "Password"},
                    "credentialKey": "password",
                }
            ],
            "formRef": "f1",
        },
    )
    approved = universal(
        request,
        approved=True,
        credential_reference="secret://integration-credential/00000000-0000-0000-0000-000000000001",
    )
    executor = BrowserGatewayExecutor(provider)
    gateway = ActionGateway((ApprovalGuard(),), executor)

    # This request is medium risk, so the guard allows it and the fake runtime
    # cannot resolve the random session. Snapshot construction occurs only on execute.
    with pytest.raises((KeyError, ExecutionProviderError)):
        await gateway.execute_request(
            principal=principal(request, risk="low"),
            request=approved,
        )

    snapshot = executor.snapshot
    assert snapshot is not None
    assert snapshot.capability_scope == "browser.auth.login"
    assert snapshot.resource_external_id == "origin:https://portal.example.test"
    assert snapshot.resource_origin == "https://portal.example.test"
    assert snapshot.provider == "browser"
    assert snapshot.adapter == "browser"
    assert snapshot.adapter_version == "1.0.0"
    assert snapshot.credential_strategy == "secret_reference"
    assert snapshot.credential_reference is not None
    assert snapshot.action_fingerprint.startswith("sha256:")


@pytest.mark.asyncio
async def test_f30_19_permissions_expose_reference_not_secret() -> None:
    provider = PlaywrightBrowserProvider(FakeGovernedRuntime())
    selected = resource(provider)

    without_secret = await provider.discover_permissions(
        resource=selected,
        configuration=selected.configuration,
        credential=None,
        credential_reference=None,
    )
    with_secret = await provider.discover_permissions(
        resource=selected,
        configuration=selected.configuration,
        credential='{"username":"divine","password":"raw-password"}',
        credential_reference="secret://integration-credential/00000000-0000-0000-0000-000000000001",
    )

    assert "browser.auth.login" not in without_secret.capability_scopes
    assert "browser.auth.login" in with_secret.capability_scopes
    assert with_secret.credential.strategy == "secret_reference"
    assert with_secret.credential.reference is not None
    assert "raw-password" not in repr(with_secret)


@pytest.mark.asyncio
async def test_f30_19_20_login_injects_runtime_secret_without_output_leak() -> None:
    runtime = FakeGovernedRuntime()
    provider = PlaywrightBrowserProvider(runtime)
    organization_id = uuid4()
    worker_id = uuid4()
    session = await runtime.create_session(
        organization_id=organization_id,
        worker_id=worker_id,
        run_id=uuid4(),
    )
    payload = await provider.normalize_input(
        operation="auth.login",
        input={
            "sessionId": str(session.id),
            "bindings": [
                {
                    "locator": {"strategy": "label", "value": "Email"},
                    "credentialKey": "username",
                },
                {
                    "locator": {"strategy": "label", "value": "Password"},
                    "credentialKey": "password",
                },
            ],
            "formRef": "f1",
        },
    )
    request = request_for(
        provider,
        operation="auth.login",
        payload=payload,
        organization_id=organization_id,
        worker_id=worker_id,
    )
    raw_secret = '{"username":"divine@example.test","password":"raw-password"}'

    result = await provider.execute(
        request=request,
        configuration=request.resource.configuration,
        credential=raw_secret,
    )

    assert runtime.last_credential_values == (
        "divine@example.test",
        "raw-password",
    )
    rendered = repr(result.output)
    assert "divine@example.test" not in rendered
    assert "raw-password" not in rendered
    assert raw_secret not in rendered
    assert "credentialKey" not in rendered


@pytest.mark.asyncio
async def test_f30_20_authentication_failure_is_normalized() -> None:
    class RejectingRuntime(FakeGovernedRuntime):
        async def authenticate(self, *args, **kwargs):
            raise BrowserAuthenticationFailure("Browser authentication was rejected.")

    runtime = RejectingRuntime()
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
        operation="auth.login",
        payload={
            "sessionId": str(session.id),
            "bindings": [
                {
                    "locator": {"strategy": "label", "value": "Password"},
                    "credentialKey": "password",
                }
            ],
            "formRef": "f1",
        },
        organization_id=organization_id,
        worker_id=worker_id,
    )

    with pytest.raises(ExecutionProviderError) as captured:
        await provider.execute(
            request=request,
            configuration=request.resource.configuration,
            credential='{"password":"wrong"}',
        )

    assert captured.value.error.code == "authentication_error"
    assert captured.value.error.retryable is False


def test_f30_19_credential_bundle_rejects_non_json_secret() -> None:
    with pytest.raises(BrowserAuthenticationFailure, match="JSON object"):
        BrowserCredentialBundle.from_secret("plain-password")


def test_f30_11_20_vercel_auto_deploy_remains_disabled() -> None:
    root = Path(__file__).resolve().parents[2]
    assert '"deploymentEnabled": false' in (root / "vercel.json").read_text(encoding="utf-8")
