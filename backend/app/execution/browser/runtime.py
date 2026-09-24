from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    Request,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

from app.execution.browser.contracts import (
    BrowserLocator,
    BrowserObservation,
    BrowserSession,
)
from app.execution.browser.credentials import (
    BrowserAuthenticationFailure,
    BrowserCredentialBundle,
)
from app.execution.browser.observation import observe_page, origin_for_url
from app.execution.browser.policy import (
    BrowserDomainPolicy,
    BrowserNavigationBlocked,
    BrowserNavigationDecision,
)

logger = logging.getLogger(__name__)


class BrowserRuntimeContract(Protocol):
    async def create_session(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        ttl_seconds: int = 1800,
        navigation_policy: BrowserDomainPolicy | None = None,
    ) -> BrowserSession: ...

    async def resume(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> BrowserSession: ...

    async def close(self, session_id: UUID) -> BrowserSession: ...

    async def expire(self, session_id: UUID) -> BrowserSession: ...

    async def terminate(self, session_id: UUID) -> BrowserSession: ...

    async def observe(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> BrowserObservation: ...

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
    ) -> BrowserObservation: ...

    async def interact(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        operation: str,
        locator: BrowserLocator | None = None,
        value: object = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation: ...

    async def fill_form(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        fields: tuple[tuple[BrowserLocator, object], ...],
        timeout_ms: int = 15_000,
    ) -> BrowserObservation: ...

    async def submit_form(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        form_ref: str | None = None,
        submit_locator: BrowserLocator | None = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation: ...

    async def authenticate(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        bindings: tuple[tuple[BrowserLocator, str], ...],
        credentials: BrowserCredentialBundle,
        form_ref: str | None = None,
        submit_locator: BrowserLocator | None = None,
        failure_text: str | None = None,
        success_url_contains: str | None = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation: ...

    async def health(self) -> bool: ...

    async def shutdown(self) -> None: ...


class _SessionHandle:
    def __init__(
        self,
        *,
        session: BrowserSession,
        context: BrowserContext,
        page: Page,
        navigation_policy: BrowserDomainPolicy | None,
    ) -> None:
        self.session = session
        self.context = context
        self.page = page
        self.navigation_policy = navigation_policy
        self.last_observation: BrowserObservation | None = None
        self.transition_trail: list[BrowserNavigationDecision] = []
        self.blocked_navigation: BrowserNavigationDecision | None = None
        self.sensitive_values: set[str] = set()


class BrowserRuntime:
    """Owns Chromium and one isolated BrowserContext per governed session."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: dict[UUID, _SessionHandle] = {}
        self._launch_lock = asyncio.Lock()

    @staticmethod
    def _redact_metadata(value: object, sensitive_values: set[str]) -> object:
        if isinstance(value, str):
            redacted = value
            for sensitive in sorted(
                (item for item in sensitive_values if item),
                key=len,
                reverse=True,
            ):
                redacted = redacted.replace(sensitive, "[REDACTED]")
            return redacted
        if isinstance(value, dict):
            return {
                str(key): BrowserRuntime._redact_metadata(item, sensitive_values)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [BrowserRuntime._redact_metadata(item, sensitive_values) for item in value]
        return value

    async def _ensure_browser(self) -> Browser:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        async with self._launch_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            try:
                self._browser = await self._playwright.chromium.launch(headless=self._headless)
            except PlaywrightError as error:
                if "Executable doesn't exist" not in str(error):
                    raise
                logger.info("Chromium is not installed; provisioning the Playwright runtime.")
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "playwright",
                    "install",
                    "chromium",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await process.communicate()
                if process.returncode != 0:
                    raise RuntimeError(
                        "Failed to provision the Chromium browser runtime: "
                        + stderr.decode("utf-8", errors="replace")[-1000:]
                    ) from error
                self._browser = await self._playwright.chromium.launch(headless=self._headless)
            return self._browser

    async def health(self) -> bool:
        try:
            browser = await self._ensure_browser()
        except (PlaywrightError, RuntimeError):
            return False
        return browser.is_connected()

    async def _route_request(
        self,
        handle: _SessionHandle,
        route: Route,
        request: Request,
    ) -> None:
        if not request.is_navigation_request():
            await route.continue_()
            return

        policy = handle.navigation_policy
        if policy is None:
            decision = BrowserNavigationDecision(
                allowed=False,
                source_url=request.frame.url or None,
                target_url=request.url,
                target_origin=origin_for_url(request.url),
                reason="Browser session has no authorized destination policy.",
                cross_origin=False,
            )
        else:
            source_url = request.frame.url
            if source_url == "about:blank":
                source_url = handle.page.url if handle.page.url != "about:blank" else None
            decision = policy.permits(request.url, source_url=source_url)

        handle.transition_trail.append(decision)
        del handle.transition_trail[:-50]
        if not decision.allowed:
            handle.blocked_navigation = decision
            await route.abort("blockedbyclient")
            return

        response = await route.fetch(max_redirects=0)
        if 300 <= response.status < 400:
            location = response.headers.get("location")
            if location:
                redirect_decision = (
                    handle.navigation_policy.permits(
                        location,
                        source_url=request.url,
                    )
                    if handle.navigation_policy is not None
                    else BrowserNavigationDecision(
                        allowed=False,
                        source_url=request.url,
                        target_url=location,
                        target_origin=origin_for_url(location),
                        reason="Browser session has no authorized destination policy.",
                        cross_origin=False,
                    )
                )
                handle.transition_trail.append(redirect_decision)
                del handle.transition_trail[:-50]
                if not redirect_decision.allowed:
                    handle.blocked_navigation = redirect_decision
                    await route.abort("blockedbyclient")
                    return
        await route.fulfill(response=response)

    async def create_session(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        ttl_seconds: int = 1800,
        navigation_policy: BrowserDomainPolicy | None = None,
    ) -> BrowserSession:
        browser = await self._ensure_browser()
        context = await browser.new_context()
        try:
            page = await context.new_page()
            now = datetime.now(UTC)
            session_id = uuid4()
            session = BrowserSession(
                id=session_id,
                organization_id=organization_id,
                worker_id=worker_id,
                run_id=run_id,
                browser_context_id=f"ctx_{uuid4().hex}",
                created_at=now,
                expires_at=now + timedelta(seconds=max(60, ttl_seconds)),
                status="active",
            )
            handle = _SessionHandle(
                session=session,
                context=context,
                page=page,
                navigation_policy=navigation_policy,
            )
            self._sessions[session_id] = handle
            await context.route(
                "**/*",
                lambda route, request: self._route_request(handle, route, request),
            )
            return session
        except BaseException:
            await context.close()
            raise

    def _handle(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> _SessionHandle:
        handle = self._sessions.get(session_id)
        if handle is None:
            raise LookupError("Browser session does not exist.")
        session = handle.session
        if session.organization_id != organization_id:
            raise PermissionError("Cross-organization browser session access denied.")
        if session.worker_id != worker_id:
            raise PermissionError("Cross-worker browser session access denied.")
        if session.status != "active":
            raise LookupError("Browser session is not active.")
        if session.expires_at <= datetime.now(UTC):
            asyncio.get_running_loop().create_task(self.expire(session_id))
            raise TimeoutError("Browser session has expired.")
        return handle

    async def resume(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> BrowserSession:
        return self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        ).session

    async def _finish(self, session_id: UUID, status: str) -> BrowserSession:
        handle = self._sessions.pop(session_id, None)
        if handle is None:
            raise LookupError("Browser session does not exist.")
        try:
            await handle.context.close()
        finally:
            handle.session = replace(
                handle.session,
                status=status,  # type: ignore[arg-type]
                current_url=handle.page.url,
                current_origin=origin_for_url(handle.page.url),
            )
            handle.sensitive_values.clear()
        return handle.session

    async def close(self, session_id: UUID) -> BrowserSession:
        return await self._finish(session_id, "closed")

    async def expire(self, session_id: UUID) -> BrowserSession:
        return await self._finish(session_id, "expired")

    async def terminate(self, session_id: UUID) -> BrowserSession:
        return await self._finish(session_id, "terminated")

    async def shutdown(self) -> None:
        for session_id in tuple(self._sessions):
            try:
                await self.terminate(session_id)
            except (LookupError, PlaywrightError) as error:
                logger.warning("Failed to terminate browser session %s: %s", session_id, error)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def observe(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> BrowserObservation:
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        observation = await observe_page(
            handle.page,
            session_id=session_id,
            sensitive_values=tuple(handle.sensitive_values),
        )
        page_state = {
            **observation.page_state,
            "navigationPolicy": {
                "lastTransitions": [
                    self._redact_metadata(item.as_dict(), handle.sensitive_values)
                    for item in handle.transition_trail[-10:]
                ],
                "blocked": (
                    self._redact_metadata(
                        handle.blocked_navigation.as_dict(),
                        handle.sensitive_values,
                    )
                    if handle.blocked_navigation is not None
                    else None
                ),
            },
        }
        observation = replace(observation, page_state=page_state)
        handle.last_observation = observation
        handle.session = replace(
            handle.session,
            current_url=observation.url,
            current_origin=origin_for_url(observation.url),
        )
        return observation

    def _locator(self, handle: _SessionHandle, locator: BrowserLocator) -> Locator:
        page = handle.page
        if locator.strategy == "role":
            return page.get_by_role(
                locator.value,  # type: ignore[arg-type]
                name=locator.name,
                exact=locator.exact,
            )
        if locator.strategy == "label":
            return page.get_by_label(locator.value, exact=locator.exact)
        if locator.strategy == "text":
            return page.get_by_text(locator.value, exact=locator.exact)
        if locator.strategy == "css":
            return page.locator(locator.value)
        if locator.strategy == "observation_ref":
            observation = handle.last_observation
            if observation is None:
                raise LookupError("No browser observation is available for this session.")
            if locator.observation_id and locator.observation_id != observation.id:
                raise LookupError("Browser locator refers to a stale observation.")
            refs = {item.ref for item in observation.elements}
            if locator.value not in refs:
                raise LookupError("Browser observation element reference was not found.")
            try:
                index = int(locator.value.removeprefix("e")) - 1
            except ValueError as error:
                raise LookupError("Browser observation element reference was invalid.") from error
            return page.locator(
                "a,button,input,textarea,select,[role],[contenteditable='true']"
            ).nth(index)
        raise ValueError("Unsupported browser locator strategy.")

    @staticmethod
    def _form_locator(handle: _SessionHandle, form_ref: str) -> Locator:
        if not form_ref.startswith("f"):
            raise ValueError("Browser form reference must use the fN observation format.")
        try:
            index = int(form_ref.removeprefix("f")) - 1
        except ValueError as error:
            raise ValueError("Browser form reference is invalid.") from error
        if index < 0:
            raise ValueError("Browser form reference is invalid.")
        return handle.page.locator("form").nth(index)

    @staticmethod
    async def _is_submit_control(target: Locator) -> bool:
        return bool(
            await target.evaluate(
                """(el) => {
                  const tag = el.tagName.toLowerCase();
                  const type = (el.getAttribute('type') || '').toLowerCase();
                  if (tag === 'input') return type === 'submit' || type === 'image';
                  if (tag === 'button') return !type || type === 'submit';
                  return false;
                }"""
            )
        )

    @staticmethod
    async def _inside_form(target: Locator) -> bool:
        return bool(await target.evaluate("(el) => Boolean(el.closest('form'))"))

    async def _set_control_value(
        self,
        target: Locator,
        value: object,
        *,
        timeout_ms: int,
    ) -> None:
        control = await target.evaluate(
            """(el) => ({
              tag: el.tagName.toLowerCase(),
              type: (el.getAttribute('type') || '').toLowerCase()
            })"""
        )
        tag = str(control.get("tag") or "") if isinstance(control, dict) else ""
        element_type = str(control.get("type") or "") if isinstance(control, dict) else ""
        if tag == "select":
            await target.select_option(str(value), timeout=timeout_ms)
            return
        if element_type in {"checkbox", "radio"}:
            if bool(value):
                await target.check(timeout=timeout_ms)
            else:
                await target.uncheck(timeout=timeout_ms)
            return
        await target.fill(str(value), timeout=timeout_ms)

    def _preflight(
        self,
        handle: _SessionHandle,
        target_url: str,
        *,
        source_url: str | None = None,
    ) -> None:
        policy = handle.navigation_policy
        if policy is None:
            raise BrowserNavigationBlocked(
                BrowserNavigationDecision(
                    allowed=False,
                    source_url=source_url,
                    target_url=target_url,
                    target_origin=origin_for_url(target_url),
                    reason="Browser session has no authorized destination policy.",
                    cross_origin=False,
                )
            )
        decision = policy.permits(
            target_url,
            source_url=source_url
            or (handle.page.url if handle.page.url != "about:blank" else None),
        )
        handle.transition_trail.append(decision)
        del handle.transition_trail[:-50]
        if not decision.allowed:
            handle.blocked_navigation = decision
            raise BrowserNavigationBlocked(decision)

    @staticmethod
    async def _settle_blocked_navigation(handle: _SessionHandle) -> None:
        # Chromium may transiently navigate to chrome-error:// after an aborted
        # main-frame request. Let that internal navigation finish before returning
        # the policy error so the same governed session can resume safely.
        await handle.page.wait_for_timeout(200)

    @staticmethod
    async def _raise_blocked(
        handle: _SessionHandle,
        error: PlaywrightError,
    ) -> None:
        if handle.blocked_navigation is not None:
            decision = handle.blocked_navigation
            handle.blocked_navigation = None
            await BrowserRuntime._settle_blocked_navigation(handle)
            raise BrowserNavigationBlocked(decision) from error
        raise error

    @staticmethod
    async def _raise_if_blocked(handle: _SessionHandle) -> None:
        if handle.blocked_navigation is not None:
            decision = handle.blocked_navigation
            handle.blocked_navigation = None
            await BrowserRuntime._settle_blocked_navigation(handle)
            raise BrowserNavigationBlocked(decision)

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
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        handle.blocked_navigation = None
        try:
            if operation == "navigation.open":
                if not url:
                    raise ValueError("URL is required for browser navigation.")
                self._preflight(handle, url)
                await handle.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            elif operation == "navigation.back":
                await handle.page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
            elif operation == "navigation.forward":
                await handle.page.go_forward(wait_until="domcontentloaded", timeout=timeout_ms)
            elif operation == "navigation.reload":
                await handle.page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
            elif operation == "navigation.follow_link":
                if locator is None:
                    raise ValueError("A locator is required to follow a link.")
                target = self._locator(handle, locator)
                href = await target.get_attribute("href")
                if href:
                    self._preflight(handle, href, source_url=handle.page.url)
                await target.click(timeout=timeout_ms)
                await handle.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            else:
                raise ValueError("Unsupported browser navigation operation.")
        except PlaywrightError as error:
            await self._raise_blocked(handle, error)
        await self._raise_if_blocked(handle)
        return await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )

    async def interact(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        operation: str,
        locator: BrowserLocator | None = None,
        value: object = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        target = self._locator(handle, locator) if locator is not None else None

        if operation == "element.click" and target is not None:
            if await self._is_submit_control(target):
                raise ValueError(
                    "Submit controls must use the governed browser.form.submit capability."
                )
            await target.click(timeout=timeout_ms)
        elif operation == "element.type" and target is not None:
            await target.fill(str(value or ""), timeout=timeout_ms)
        elif operation == "element.clear" and target is not None:
            await target.fill("", timeout=timeout_ms)
        elif operation == "element.select" and target is not None:
            await target.select_option(str(value or ""), timeout=timeout_ms)
        elif operation == "element.check" and target is not None:
            await target.check(timeout=timeout_ms)
        elif operation == "element.uncheck" and target is not None:
            await target.uncheck(timeout=timeout_ms)
        elif operation == "element.press_key" and target is not None:
            if str(value).lower() in {"enter", "numpadenter"} and await self._inside_form(target):
                raise ValueError("Form submission by keyboard must use browser.form.submit.")
            await target.press(str(value or ""), timeout=timeout_ms)
        elif operation == "element.hover" and target is not None:
            await target.hover(timeout=timeout_ms)
        elif operation == "page.scroll":
            amount = int(value) if isinstance(value, (int, float, str)) else 0
            await handle.page.mouse.wheel(0, amount)
        else:
            raise ValueError("Unsupported browser interaction operation.")

        return await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )

    async def fill_form(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        fields: tuple[tuple[BrowserLocator, object], ...],
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        if not fields:
            raise ValueError("At least one form field is required.")
        for locator, value in fields:
            target = self._locator(handle, locator)
            if await self._is_submit_control(target):
                raise ValueError("Form fill cannot target a submit control.")
            await self._set_control_value(target, value, timeout_ms=timeout_ms)
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
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        handle.blocked_navigation = None
        if form_ref is None and submit_locator is None:
            raise ValueError("Form submission requires formRef or submitLocator.")

        try:
            if submit_locator is not None:
                target = self._locator(handle, submit_locator)
                if not await self._is_submit_control(target):
                    raise ValueError("submitLocator does not identify a form submit control.")
                form_action = await target.evaluate("(el) => el.form ? el.form.action : null")
                if form_action:
                    self._preflight(handle, str(form_action), source_url=handle.page.url)
                await target.click(timeout=timeout_ms)
            else:
                assert form_ref is not None
                form = self._form_locator(handle, form_ref)
                form_action = await form.get_attribute("action")
                if form_action:
                    self._preflight(handle, form_action, source_url=handle.page.url)
                await form.evaluate("(form) => form.requestSubmit()")
            await handle.page.wait_for_timeout(50)
        except PlaywrightError as error:
            await self._raise_blocked(handle, error)

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
        bindings: tuple[tuple[BrowserLocator, str], ...],
        credentials: BrowserCredentialBundle,
        form_ref: str | None = None,
        submit_locator: BrowserLocator | None = None,
        failure_text: str | None = None,
        success_url_contains: str | None = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        if not bindings:
            raise BrowserAuthenticationFailure(
                "Browser authentication requires credential field bindings."
            )

        form_method: str | None = None
        if form_ref is not None:
            form_method = (
                await self._form_locator(handle, form_ref).get_attribute("method")
            ) or "get"
        elif submit_locator is not None:
            submit_target = self._locator(handle, submit_locator)
            raw_method = await submit_target.evaluate(
                "(el) => el.form ? (el.form.method || 'get') : null"
            )
            form_method = str(raw_method) if raw_method is not None else None
        if form_method is not None and form_method.lower() == "get":
            raise BrowserAuthenticationFailure(
                "Browser authentication refuses GET form submission because credentials could enter the URL."
            )

        handle.sensitive_values.update(credentials.sensitive_values())
        for locator, credential_key in bindings:
            target = self._locator(handle, locator)
            await self._set_control_value(
                target,
                credentials.resolve(credential_key),
                timeout_ms=timeout_ms,
            )

        observation = await self.submit_form(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
            form_ref=form_ref,
            submit_locator=submit_locator,
            timeout_ms=timeout_ms,
        )
        if failure_text and failure_text in observation.visible_text:
            raise BrowserAuthenticationFailure("Browser authentication was rejected.")
        if success_url_contains and success_url_contains not in observation.url:
            raise BrowserAuthenticationFailure(
                "Browser authentication did not reach the expected destination."
            )
        return observation
