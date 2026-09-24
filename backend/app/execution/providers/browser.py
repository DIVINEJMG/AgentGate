from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.execution.authorization import CredentialReference, ProviderPermissionSnapshot
from app.execution.browser.contracts import (
    BrowserLocator,
    BrowserObservation,
    BrowserSession,
)
from app.execution.browser.credentials import (
    BrowserAuthenticationFailure,
    BrowserCredentialBundle,
)
from app.execution.browser.policy import (
    BrowserDomainPolicy,
    BrowserNavigationBlocked,
)
from app.execution.browser.runtime import BrowserRuntime, BrowserRuntimeContract
from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionProviderError,
    ExecutionRequest,
    ExecutionResult,
    ProviderHealth,
    ProviderManifest,
    ResourceDescriptor,
    VerificationResult,
)
from app.execution.providers.base import ExecutionProvider

FILE_TRANSFER_SCOPES = frozenset({"browser.file.upload", "browser.file.download"})


def _capability(
    *,
    scope: str,
    operation: str,
    mode: str,
    risk: str,
    side_effect: bool,
    approval: str,
    description: str,
    requires_locator: bool = False,
    requires_value: bool = False,
    requires_credential: bool = False,
    properties: dict[str, object] | None = None,
    required: tuple[str, ...] = (),
    target: str = "page",
) -> CapabilityDescriptor:
    input_properties: dict[str, object] = {
        "sessionId": {"type": "string", "format": "uuid"},
    }
    input_required: list[str] = ["sessionId"]
    if operation == "navigation.open":
        input_properties["url"] = {"type": "string", "format": "uri"}
        input_required = ["url"]
    if requires_locator:
        input_properties["locator"] = {"type": "object"}
        input_required.append("locator")
    if requires_value:
        input_properties["value"] = {}
        input_required.append("value")
    if properties:
        input_properties.update(properties)
    input_required.extend(item for item in required if item not in input_required)
    return CapabilityDescriptor(
        scope=scope,
        provider="browser",
        resource_type="web",
        operation=operation,
        mode=mode,  # type: ignore[arg-type]
        risk=risk,  # type: ignore[arg-type]
        requires_credential=requires_credential,
        side_effect=side_effect,
        approval_recommendation=approval,  # type: ignore[arg-type]
        input_schema={
            "type": "object",
            "properties": input_properties,
            "required": input_required,
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "session": {"type": "object"},
                "observation": {"type": "object"},
            },
        },
        description=description,
        target=target,
    )


CAPABILITIES = (
    _capability(
        scope="browser.page.read",
        operation="page.observe",
        mode="read",
        risk="low",
        side_effect=False,
        approval="none",
        description="Observe the current governed browser page.",
    ),
    _capability(
        scope="browser.navigation.open",
        operation="navigation.open",
        mode="action",
        risk="low",
        side_effect=False,
        approval="none",
        description="Open an authorized URL in an isolated governed browser session.",
    ),
    _capability(
        scope="browser.navigation.back",
        operation="navigation.back",
        mode="action",
        risk="low",
        side_effect=False,
        approval="none",
        description="Navigate backward within the governed browser session.",
    ),
    _capability(
        scope="browser.navigation.forward",
        operation="navigation.forward",
        mode="action",
        risk="low",
        side_effect=False,
        approval="none",
        description="Navigate forward within the governed browser session.",
    ),
    _capability(
        scope="browser.navigation.reload",
        operation="navigation.reload",
        mode="action",
        risk="low",
        side_effect=False,
        approval="none",
        description="Reload the current governed page.",
    ),
    _capability(
        scope="browser.navigation.follow_link",
        operation="navigation.follow_link",
        mode="action",
        risk="low",
        side_effect=False,
        approval="none",
        description="Follow an authorized link selected by a normalized browser locator.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.click",
        operation="element.click",
        mode="action",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Click a non-submit element selected by a normalized browser locator.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.type",
        operation="element.type",
        mode="write",
        risk="medium",
        side_effect=False,
        approval="none",
        description="Edit a single non-secret browser field without submitting its form.",
        requires_locator=True,
        requires_value=True,
    ),
    _capability(
        scope="browser.element.clear",
        operation="element.clear",
        mode="write",
        risk="medium",
        side_effect=False,
        approval="none",
        description="Clear a single editable browser field without submitting its form.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.select",
        operation="element.select",
        mode="write",
        risk="medium",
        side_effect=False,
        approval="none",
        description="Select an option without submitting its form.",
        requires_locator=True,
        requires_value=True,
    ),
    _capability(
        scope="browser.element.check",
        operation="element.check",
        mode="write",
        risk="medium",
        side_effect=False,
        approval="none",
        description="Check a checkbox or radio field without submitting its form.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.uncheck",
        operation="element.uncheck",
        mode="write",
        risk="medium",
        side_effect=False,
        approval="none",
        description="Uncheck a checkbox without submitting its form.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.press_key",
        operation="element.press_key",
        mode="action",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Press a key outside form-submission shortcuts.",
        requires_locator=True,
        requires_value=True,
    ),
    _capability(
        scope="browser.page.scroll",
        operation="page.scroll",
        mode="action",
        risk="low",
        side_effect=False,
        approval="none",
        description="Scroll the governed page vertically.",
        requires_value=True,
    ),
    _capability(
        scope="browser.element.hover",
        operation="element.hover",
        mode="action",
        risk="low",
        side_effect=False,
        approval="none",
        description="Hover over a selected element.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.form.fill",
        operation="form.fill",
        mode="write",
        risk="medium",
        side_effect=False,
        approval="none",
        description="Fill structured form fields without submitting the form.",
        properties={
            "fields": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {"locator": {"type": "object"}, "value": {}},
                    "required": ["locator", "value"],
                    "additionalProperties": False,
                },
            }
        },
        required=("fields",),
        target="form",
    ),
    _capability(
        scope="browser.form.submit",
        operation="form.submit",
        mode="action",
        risk="high",
        side_effect=True,
        approval="recommended",
        description="Submit a governed form after higher-risk policy evaluation.",
        properties={
            "formRef": {"type": "string"},
            "submitLocator": {"type": "object"},
        },
        target="form",
    ),
    _capability(
        scope="browser.auth.login",
        operation="auth.login",
        mode="action",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Inject vaulted credentials into an authorized login form at runtime.",
        requires_credential=True,
        properties={
            "bindings": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "locator": {"type": "object"},
                        "credentialKey": {"type": "string"},
                    },
                    "required": ["locator", "credentialKey"],
                    "additionalProperties": False,
                },
            },
            "formRef": {"type": "string"},
            "submitLocator": {"type": "object"},
            "failureText": {"type": "string"},
            "successUrlContains": {"type": "string"},
        },
        required=("bindings",),
        target="authentication",
    ),
    _capability(
        scope="browser.file.upload",
        operation="file.upload",
        mode="write",
        risk="high",
        side_effect=True,
        approval="required",
        description="Upload an authorized Aduoryn artifact through the governed browser boundary.",
        properties={
            "artifactId": {"type": "string", "format": "uuid"},
            "locator": {"type": "object"},
        },
        required=("artifactId", "locator"),
        target="file",
    ),
    _capability(
        scope="browser.file.download",
        operation="file.download",
        mode="action",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Download through the governed browser artifact boundary.",
        properties={"locator": {"type": "object"}},
        required=("locator",),
        target="file",
    ),
)


@runtime_checkable
class BrowserExecutionProvider(ExecutionProvider, Protocol):
    async def open_session(self, request: ExecutionRequest) -> BrowserSession: ...

    async def discover_page_capabilities(
        self,
        *,
        session: BrowserSession,
        request: ExecutionRequest,
    ) -> tuple[CapabilityDescriptor, ...]: ...

    async def observe(
        self,
        *,
        session: BrowserSession,
        request: ExecutionRequest,
    ) -> BrowserObservation: ...

    async def close_session(self, session: BrowserSession) -> BrowserSession: ...

    async def recover(
        self,
        *,
        session: BrowserSession,
        request: ExecutionRequest,
        result: ExecutionResult | None,
        observation: dict[str, object],
    ) -> dict[str, object]: ...


class PlaywrightBrowserProvider:
    manifest = ProviderManifest(
        provider="browser",
        display_name="Governed Browser",
        kind="browser",
        version="1.0.0",
        credential_strategy="secret_reference",
        capabilities=CAPABILITIES,
    )

    def __init__(self, runtime: BrowserRuntimeContract | None = None) -> None:
        self._runtime = runtime or BrowserRuntime()

    @staticmethod
    def _domain_policy(
        configuration: dict[str, str],
        *,
        fallback_url: str | None = None,
    ) -> BrowserDomainPolicy:
        return BrowserDomainPolicy.from_configuration(
            configuration,
            fallback_url=fallback_url,
        )

    async def discover_resources(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> tuple[ResourceDescriptor, ...]:
        del credential
        start_url = configuration.get("startUrl")
        policy = self._domain_policy(configuration, fallback_url=start_url)
        primary_origin = policy.primary_origin
        if primary_origin is None:
            raise ValueError(
                "Browser resource requires startUrl or an explicit allowedOrigins policy."
            )

        external_id = f"origin:{primary_origin}"
        display_name = (
            configuration.get("displayName", "").strip()
            or configuration.get("resourceKey", "").strip()
            or primary_origin
        )
        file_transfer_enabled = (
            configuration.get("enableFileTransfer", "false").strip().lower() == "true"
        )
        available = tuple(
            item.scope
            for item in self.manifest.capabilities
            if file_transfer_enabled or item.scope not in FILE_TRANSFER_SCOPES
        )
        return (
            ResourceDescriptor(
                id=f"browser:{external_id}",
                provider="browser",
                resource_type="origin",
                external_id=external_id,
                display_name=display_name,
                metadata={
                    "origin": primary_origin,
                    "allowedOrigins": list(policy.allowed_origins),
                    "deniedOrigins": list(policy.denied_origins),
                    "allowedPaths": list(policy.allowed_path_prefixes),
                    "deniedPaths": list(policy.denied_path_prefixes),
                    "fileTransferEnabled": file_transfer_enabled,
                },
                health="healthy",
                available_capabilities=available,
                web_url=start_url or primary_origin,
                configuration=dict(configuration),
            ),
        )

    async def discover_capabilities(
        self,
        *,
        resource: ResourceDescriptor | None = None,
    ) -> tuple[CapabilityDescriptor, ...]:
        if resource is not None and resource.provider != self.manifest.provider:
            return ()
        if resource is None:
            return self.manifest.capabilities
        allowed = set(resource.available_capabilities)
        return tuple(item for item in self.manifest.capabilities if item.scope in allowed)

    async def check_health(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ProviderHealth:
        del credential
        try:
            policy = self._domain_policy(
                configuration,
                fallback_url=configuration.get("startUrl"),
            )
        except ValueError as error:
            return ProviderHealth(
                state="unavailable",
                message=str(error),
                checked_at=datetime.now(UTC),
                metadata={"engine": "chromium", "policyConfigured": False},
            )
        healthy = bool(policy.allowed_origins) and await self._runtime.health()
        return ProviderHealth(
            state="healthy" if healthy else "unavailable",
            message=(
                "Chromium browser runtime and destination policy are available."
                if healthy
                else "Chromium browser runtime or destination policy is unavailable."
            ),
            checked_at=datetime.now(UTC),
            metadata={
                "engine": "chromium",
                "headless": True,
                "policyConfigured": bool(policy.allowed_origins),
                "allowedOrigins": list(policy.allowed_origins),
            },
        )

    async def discover_permissions(
        self,
        *,
        resource: ResourceDescriptor,
        configuration: dict[str, str],
        credential: str | None,
        credential_reference: str | None,
    ) -> ProviderPermissionSnapshot:
        del credential
        if resource.provider != self.manifest.provider:
            raise ValueError("Permission discovery resource belongs to another provider.")
        declared = set(resource.available_capabilities)
        configured_raw = configuration.get("permissionScopes", "").strip()
        configured = {item.strip() for item in configured_raw.split(",") if item.strip()}
        if configured:
            declared &= configured
        if credential_reference is None:
            declared -= {
                item.scope for item in self.manifest.capabilities if item.requires_credential
            }
            credential_handle = CredentialReference(strategy="none", reference=None)
        else:
            credential_handle = CredentialReference(
                strategy="secret_reference",
                reference=credential_reference,
            )
        allowed = tuple(
            sorted(item.scope for item in self.manifest.capabilities if item.scope in declared)
        )
        return ProviderPermissionSnapshot(
            provider=self.manifest.provider,
            resource_id=resource.id,
            capability_scopes=allowed,
            credential=credential_handle,
            checked_at=datetime.now(UTC),
            adapter=self.manifest.kind,
            adapter_version=self.manifest.version,
            metadata={
                "configured_scope_filter": bool(configured),
                "engine": "chromium",
                "origin": resource.metadata.get("origin"),
                "credentialConfigured": credential_reference is not None,
            },
        )

    def _error(
        self,
        *,
        request: ExecutionRequest,
        code,
        retryable: bool,
        safe_message: str,
        internal_details: str | None = None,
    ) -> ExecutionProviderError:
        return ExecutionProviderError(
            code=code,
            retryable=retryable,
            provider=self.manifest.provider,
            operation=request.operation,
            correlation_id=request.correlation_id,
            safe_message=safe_message,
            internal_details=internal_details,
        )

    @staticmethod
    def _session_id(payload: dict[str, object]) -> UUID | None:
        raw = payload.get("sessionId")
        if raw in (None, ""):
            return None
        try:
            return UUID(str(raw))
        except ValueError as error:
            raise ValueError("Browser sessionId must be a UUID.") from error

    @staticmethod
    def _locator_from(value: object) -> BrowserLocator:
        return BrowserLocator.from_mapping(value)

    @classmethod
    def _locator(cls, payload: dict[str, object], key: str = "locator") -> BrowserLocator | None:
        value = payload.get(key)
        return cls._locator_from(value) if value is not None else None

    @classmethod
    def _form_fields(
        cls,
        payload: dict[str, object],
    ) -> tuple[tuple[BrowserLocator, object], ...]:
        raw = payload.get("fields")
        if not isinstance(raw, list) or not raw:
            raise ValueError("Browser form fields must be a non-empty array.")
        fields: list[tuple[BrowserLocator, object]] = []
        for item in raw:
            if not isinstance(item, dict) or set(item) - {"locator", "value"}:
                raise ValueError("Browser form field is invalid.")
            if "locator" not in item or "value" not in item:
                raise ValueError("Browser form field requires locator and value.")
            fields.append((cls._locator_from(item["locator"]), item["value"]))
        return tuple(fields)

    @classmethod
    def _credential_bindings(
        cls,
        payload: dict[str, object],
    ) -> tuple[tuple[BrowserLocator, str], ...]:
        raw = payload.get("bindings")
        if not isinstance(raw, list) or not raw:
            raise ValueError("Browser credential bindings must be a non-empty array.")
        bindings: list[tuple[BrowserLocator, str]] = []
        for item in raw:
            if not isinstance(item, dict) or set(item) - {"locator", "credentialKey"}:
                raise ValueError("Browser credential binding is invalid.")
            key = str(item.get("credentialKey", "")).strip()
            if "locator" not in item or not key:
                raise ValueError("Browser credential binding requires locator and credentialKey.")
            bindings.append((cls._locator_from(item["locator"]), key))
        return tuple(bindings)

    async def normalize_input(
        self,
        *,
        operation: str,
        input: dict[str, object],
    ) -> dict[str, object]:
        allowed: dict[str, set[str]] = {
            "page.observe": {"sessionId"},
            "navigation.open": {"url", "sessionId"},
            "navigation.back": {"sessionId"},
            "navigation.forward": {"sessionId"},
            "navigation.reload": {"sessionId"},
            "navigation.follow_link": {"sessionId", "locator"},
            "element.click": {"sessionId", "locator"},
            "element.type": {"sessionId", "locator", "value"},
            "element.clear": {"sessionId", "locator"},
            "element.select": {"sessionId", "locator", "value"},
            "element.check": {"sessionId", "locator"},
            "element.uncheck": {"sessionId", "locator"},
            "element.press_key": {"sessionId", "locator", "value"},
            "page.scroll": {"sessionId", "value"},
            "element.hover": {"sessionId", "locator"},
            "form.fill": {"sessionId", "fields"},
            "form.submit": {"sessionId", "formRef", "submitLocator"},
            "auth.login": {
                "sessionId",
                "bindings",
                "formRef",
                "submitLocator",
                "failureText",
                "successUrlContains",
            },
            "file.upload": {"sessionId", "artifactId", "locator"},
            "file.download": {"sessionId", "locator"},
        }
        supported = allowed.get(operation)
        if supported is None:
            raise ValueError("Unsupported browser operation.")
        unknown = set(input) - supported
        if unknown:
            raise ValueError(f"Unsupported browser input field: {min(unknown)}.")

        normalized = dict(input)
        session_id = self._session_id(normalized)
        if session_id is not None:
            normalized["sessionId"] = str(session_id)

        if operation == "navigation.open":
            url = str(normalized.get("url", "")).strip()
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError("Browser URL must be an absolute HTTP or HTTPS URL.")
            normalized["url"] = url
        elif session_id is None:
            raise ValueError("Browser sessionId is required for this operation.")

        needs_locator = operation in {
            "navigation.follow_link",
            "element.click",
            "element.type",
            "element.clear",
            "element.select",
            "element.check",
            "element.uncheck",
            "element.press_key",
            "element.hover",
            "file.upload",
            "file.download",
        }
        if needs_locator:
            locator = self._locator(normalized)
            if locator is None:
                raise ValueError("Browser locator is required for this operation.")
            normalized["locator"] = locator.as_dict()

        needs_value = operation in {
            "element.type",
            "element.select",
            "element.press_key",
            "page.scroll",
        }
        if needs_value and "value" not in normalized:
            raise ValueError("Browser value is required for this operation.")

        if operation == "form.fill":
            fields = self._form_fields(normalized)
            normalized["fields"] = [
                {"locator": locator.as_dict(), "value": value} for locator, value in fields
            ]
        elif operation == "form.submit":
            form_ref = str(normalized.get("formRef", "")).strip()
            locator = self._locator(normalized, "submitLocator")
            if not form_ref and locator is None:
                raise ValueError("Form submission requires formRef or submitLocator.")
            if form_ref:
                normalized["formRef"] = form_ref
            if locator is not None:
                normalized["submitLocator"] = locator.as_dict()
        elif operation == "auth.login":
            bindings = self._credential_bindings(normalized)
            normalized["bindings"] = [
                {"locator": locator.as_dict(), "credentialKey": key} for locator, key in bindings
            ]
            form_ref = str(normalized.get("formRef", "")).strip()
            submit = self._locator(normalized, "submitLocator")
            if not form_ref and submit is None:
                raise ValueError("Browser authentication requires formRef or submitLocator.")
            if form_ref:
                normalized["formRef"] = form_ref
            if submit is not None:
                normalized["submitLocator"] = submit.as_dict()
        elif operation == "file.upload":
            try:
                UUID(str(normalized.get("artifactId", "")))
            except ValueError as error:
                raise ValueError("Browser upload artifactId must be a UUID.") from error

        return normalized

    async def open_session(self, request: ExecutionRequest) -> BrowserSession:
        fallback_url = request.resource.web_url
        if fallback_url is None and request.resource.external_id.startswith("origin:"):
            fallback_url = request.resource.external_id.removeprefix("origin:")
        policy = self._domain_policy(
            request.resource.configuration,
            fallback_url=fallback_url,
        )
        if not policy.allowed_origins:
            raise BrowserNavigationBlocked(
                policy.permits(
                    str(request.input.get("url") or ""),
                    source_url=None,
                )
            )
        return await self._runtime.create_session(
            organization_id=request.organization_id,
            worker_id=request.worker_id,
            run_id=request.run_id,
            navigation_policy=policy,
        )

    async def close_session(self, session: BrowserSession) -> BrowserSession:
        return await self._runtime.close(session.id)

    async def shutdown(self) -> None:
        await self._runtime.shutdown()

    async def discover_page_capabilities(
        self,
        *,
        session: BrowserSession,
        request: ExecutionRequest,
    ) -> tuple[CapabilityDescriptor, ...]:
        await self._runtime.resume(
            session.id,
            organization_id=request.organization_id,
            worker_id=request.worker_id,
        )
        allowed = set(request.resource.available_capabilities)
        return tuple(item for item in self.manifest.capabilities if item.scope in allowed)

    async def observe(
        self,
        *,
        session: BrowserSession,
        request: ExecutionRequest,
    ) -> BrowserObservation:
        return await self._runtime.observe(
            session.id,
            organization_id=request.organization_id,
            worker_id=request.worker_id,
        )

    async def execute(
        self,
        *,
        request: ExecutionRequest,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ExecutionResult:
        started_at = datetime.now(UTC)
        session_id: UUID | None = None
        session_owned = False
        try:
            if request.operation in {"file.upload", "file.download"}:
                raise self._error(
                    request=request,
                    code="unsupported_operation",
                    retryable=False,
                    safe_message=(
                        "Browser file transfer is catalogued but remains disabled until "
                        "the governed artifact boundary is enabled."
                    ),
                )

            session_id = self._session_id(request.input)
            if session_id is None:
                if request.operation != "navigation.open":
                    raise ValueError("A browser session is required for this operation.")
                session = await self.open_session(request)
                session_id = session.id
                session_owned = True
            else:
                session = await self._runtime.resume(
                    session_id,
                    organization_id=request.organization_id,
                    worker_id=request.worker_id,
                )
                session_owned = True

            timeout_ms = int(configuration.get("actionTimeoutMs", "15000"))
            if request.operation == "page.observe":
                observation = await self._runtime.observe(
                    session_id,
                    organization_id=request.organization_id,
                    worker_id=request.worker_id,
                )
            elif request.operation.startswith("navigation."):
                observation = await self._runtime.navigate(
                    session_id,
                    organization_id=request.organization_id,
                    worker_id=request.worker_id,
                    operation=request.operation,
                    url=str(request.input.get("url")) if request.input.get("url") else None,
                    locator=self._locator(request.input),
                    timeout_ms=int(configuration.get("navigationTimeoutMs", "30000")),
                )
            elif request.operation == "form.fill":
                observation = await self._runtime.fill_form(
                    session_id,
                    organization_id=request.organization_id,
                    worker_id=request.worker_id,
                    fields=self._form_fields(request.input),
                    timeout_ms=timeout_ms,
                )
            elif request.operation == "form.submit":
                observation = await self._runtime.submit_form(
                    session_id,
                    organization_id=request.organization_id,
                    worker_id=request.worker_id,
                    form_ref=(
                        str(request.input["formRef"]) if request.input.get("formRef") else None
                    ),
                    submit_locator=self._locator(request.input, "submitLocator"),
                    timeout_ms=timeout_ms,
                )
            elif request.operation == "auth.login":
                credentials = BrowserCredentialBundle.from_secret(credential)
                observation = await self._runtime.authenticate(
                    session_id,
                    organization_id=request.organization_id,
                    worker_id=request.worker_id,
                    bindings=self._credential_bindings(request.input),
                    credentials=credentials,
                    form_ref=(
                        str(request.input["formRef"]) if request.input.get("formRef") else None
                    ),
                    submit_locator=self._locator(request.input, "submitLocator"),
                    failure_text=(
                        str(request.input["failureText"])
                        if request.input.get("failureText")
                        else None
                    ),
                    success_url_contains=(
                        str(request.input["successUrlContains"])
                        if request.input.get("successUrlContains")
                        else None
                    ),
                    timeout_ms=timeout_ms,
                )
            else:
                observation = await self._runtime.interact(
                    session_id,
                    organization_id=request.organization_id,
                    worker_id=request.worker_id,
                    operation=request.operation,
                    locator=self._locator(request.input),
                    value=request.input.get("value"),
                    timeout_ms=timeout_ms,
                )

            current = await self._runtime.resume(
                session_id,
                organization_id=request.organization_id,
                worker_id=request.worker_id,
            )
            return ExecutionResult.successful(
                provider=self.manifest.provider,
                adapter=self.manifest.kind,
                adapter_version=self.manifest.version,
                operation=request.operation,
                output={
                    "session": current.as_dict(),
                    "observation": observation.as_dict(),
                },
                provider_request_id=str(session_id),
                started_at=started_at,
            )
        except asyncio.CancelledError:
            if session_owned and session_id is not None:
                await self._terminate_quietly(session_id)
            raise
        except BrowserNavigationBlocked as error:
            raise self._error(
                request=request,
                code="policy_blocked",
                retryable=False,
                safe_message="Browser navigation was blocked by destination policy.",
                internal_details=str(error),
            ) from error
        except BrowserAuthenticationFailure as error:
            raise self._error(
                request=request,
                code="authentication_error",
                retryable=False,
                safe_message=str(error),
            ) from error
        except ExecutionProviderError:
            raise
        except PermissionError as error:
            if session_owned and session_id is not None:
                await self._terminate_quietly(session_id)
            raise self._error(
                request=request,
                code="authorization_error",
                retryable=False,
                safe_message="Browser session access was denied.",
                internal_details=str(error),
            ) from error
        except (PlaywrightTimeoutError, TimeoutError) as error:
            if session_owned and session_id is not None:
                await self._terminate_quietly(session_id)
            raise self._error(
                request=request,
                code="timeout",
                retryable=True,
                safe_message="Browser operation timed out.",
                internal_details=str(error),
            ) from error
        except LookupError as error:
            if session_owned and session_id is not None:
                await self._terminate_quietly(session_id)
            raise self._error(
                request=request,
                code="resource_not_found",
                retryable=False,
                safe_message="Browser session or target element was not found.",
                internal_details=str(error),
            ) from error
        except ValueError as error:
            if session_owned and session_id is not None:
                await self._terminate_quietly(session_id)
            raise self._error(
                request=request,
                code="validation_error",
                retryable=False,
                safe_message=str(error),
            ) from error
        except PlaywrightError as error:
            if session_owned and session_id is not None:
                await self._terminate_quietly(session_id)
            raise self._error(
                request=request,
                code="temporary_provider_error",
                retryable=True,
                safe_message="Browser runtime failed to complete the operation.",
                internal_details=str(error),
            ) from error

    async def _terminate_quietly(self, session_id: UUID) -> None:
        try:
            await self._runtime.terminate(session_id)
        except (LookupError, PlaywrightError):
            return

    async def recover(
        self,
        *,
        session: BrowserSession,
        request: ExecutionRequest,
        result: ExecutionResult | None,
        observation: dict[str, object],
    ) -> dict[str, object]:
        del result, observation
        current = await self.observe(session=session, request=request)
        return {
            "action": "reobserve",
            "sessionId": str(session.id),
            "observation": current.as_dict(),
        }

    async def verify(
        self,
        *,
        request: ExecutionRequest,
        result: ExecutionResult,
        configuration: dict[str, str],
        credential: str | None,
    ) -> VerificationResult:
        del configuration, credential
        output = result.output if isinstance(result.output, dict) else {}
        observation = output.get("observation")
        session = output.get("session")
        verified = (
            result.status == "success"
            and isinstance(observation, dict)
            and isinstance(session, dict)
        )
        return VerificationResult(
            verified=verified,
            summary=(
                "Browser operation completed and produced a normalized page observation."
                if verified
                else "Browser operation did not produce a valid observation."
            ),
            details={
                "provider": self.manifest.provider,
                "operation": request.operation,
                "sessionId": session.get("id") if isinstance(session, dict) else None,
                "url": observation.get("url") if isinstance(observation, dict) else None,
                "resourceOrigin": request.resource.metadata.get("origin"),
            },
        )


browser_provider = PlaywrightBrowserProvider()
