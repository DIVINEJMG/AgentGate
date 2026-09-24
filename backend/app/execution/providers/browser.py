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
) -> CapabilityDescriptor:
    properties: dict[str, object] = {
        "sessionId": {"type": "string", "format": "uuid"},
    }
    required: list[str] = ["sessionId"]
    if operation == "navigation.open":
        properties["url"] = {"type": "string", "format": "uri"}
        required = ["url"]
    if requires_locator:
        properties["locator"] = {"type": "object"}
        required.append("locator")
    if requires_value:
        properties["value"] = {}
        required.append("value")
    return CapabilityDescriptor(
        scope=scope,
        provider="browser",
        resource_type="web",
        operation=operation,
        mode=mode,  # type: ignore[arg-type]
        risk=risk,  # type: ignore[arg-type]
        requires_credential=False,
        side_effect=side_effect,
        approval_recommendation=approval,  # type: ignore[arg-type]
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        description=description,
        target="page",
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
        description="Open a URL in an isolated governed browser session.",
    ),
    _capability(
        scope="browser.navigation.back",
        operation="navigation.back",
        mode="action",
        risk="low",
        side_effect=False,
        approval="none",
        description="Navigate backward in the governed browser session.",
    ),
    _capability(
        scope="browser.navigation.forward",
        operation="navigation.forward",
        mode="action",
        risk="low",
        side_effect=False,
        approval="none",
        description="Navigate forward in the governed browser session.",
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
        description="Follow a link selected by a normalized browser locator.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.click",
        operation="element.click",
        mode="action",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Click an element selected by a normalized browser locator.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.type",
        operation="element.type",
        mode="write",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Type into an editable element.",
        requires_locator=True,
        requires_value=True,
    ),
    _capability(
        scope="browser.element.clear",
        operation="element.clear",
        mode="write",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Clear an editable element.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.select",
        operation="element.select",
        mode="write",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Select an option in a select element.",
        requires_locator=True,
        requires_value=True,
    ),
    _capability(
        scope="browser.element.check",
        operation="element.check",
        mode="write",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Check a checkbox or radio element.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.uncheck",
        operation="element.uncheck",
        mode="write",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Uncheck a checkbox element.",
        requires_locator=True,
    ),
    _capability(
        scope="browser.element.press_key",
        operation="element.press_key",
        mode="action",
        risk="medium",
        side_effect=True,
        approval="recommended",
        description="Press a keyboard key while an element is targeted.",
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
        credential_strategy="none",
        capabilities=CAPABILITIES,
    )

    def __init__(self, runtime: BrowserRuntimeContract | None = None) -> None:
        self._runtime = runtime or BrowserRuntime()

    async def discover_resources(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> tuple[ResourceDescriptor, ...]:
        del credential
        external_id = configuration.get("resourceKey", "governed-web").strip() or "governed-web"
        display_name = configuration.get("displayName", "Governed Web").strip() or "Governed Web"
        start_url = configuration.get("startUrl")
        return (
            ResourceDescriptor(
                id=f"browser:{external_id}",
                provider="browser",
                resource_type="web",
                external_id=external_id,
                display_name=display_name,
                metadata={"startUrl": start_url} if start_url else {},
                health="healthy",
                available_capabilities=tuple(item.scope for item in self.manifest.capabilities),
                web_url=start_url,
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
        return self.manifest.capabilities

    async def check_health(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ProviderHealth:
        del configuration, credential
        healthy = await self._runtime.health()
        return ProviderHealth(
            state="healthy" if healthy else "unavailable",
            message=(
                "Chromium browser runtime is available."
                if healthy
                else "Chromium browser runtime is unavailable."
            ),
            checked_at=datetime.now(UTC),
            metadata={"engine": "chromium", "headless": True},
        )

    async def discover_permissions(
        self,
        *,
        resource: ResourceDescriptor,
        configuration: dict[str, str],
        credential: str | None,
        credential_reference: str | None,
    ) -> ProviderPermissionSnapshot:
        del credential, credential_reference
        if resource.provider != self.manifest.provider:
            raise ValueError("Permission discovery resource belongs to another provider.")
        declared = set(resource.available_capabilities)
        configured_raw = configuration.get("permissionScopes", "").strip()
        configured = {item.strip() for item in configured_raw.split(",") if item.strip()}
        if configured:
            declared &= configured
        allowed = tuple(
            sorted(item.scope for item in self.manifest.capabilities if item.scope in declared)
        )
        return ProviderPermissionSnapshot(
            provider=self.manifest.provider,
            resource_id=resource.id,
            capability_scopes=allowed,
            credential=CredentialReference(strategy="none", reference=None),
            checked_at=datetime.now(UTC),
            adapter=self.manifest.kind,
            adapter_version=self.manifest.version,
            metadata={"configured_scope_filter": bool(configured), "engine": "chromium"},
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
    def _locator(payload: dict[str, object]) -> BrowserLocator | None:
        value = payload.get("locator")
        return BrowserLocator.from_mapping(value) if value is not None else None

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
        return normalized

    async def open_session(self, request: ExecutionRequest) -> BrowserSession:
        return await self._runtime.create_session(
            organization_id=request.organization_id,
            worker_id=request.worker_id,
            run_id=request.run_id,
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
        return self.manifest.capabilities

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
        del credential
        started_at = datetime.now(UTC)
        session_id: UUID | None = None
        session_owned = False
        try:
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
            },
        )


browser_provider = PlaywrightBrowserProvider()
