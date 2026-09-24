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
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

from app.execution.browser.contracts import (
    BrowserLocator,
    BrowserObservation,
    BrowserSession,
)
from app.execution.browser.observation import observe_page, origin_for_url

logger = logging.getLogger(__name__)


class BrowserRuntimeContract(Protocol):
    async def create_session(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        ttl_seconds: int = 1800,
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

    async def health(self) -> bool: ...

    async def shutdown(self) -> None: ...


class _SessionHandle:
    def __init__(
        self,
        *,
        session: BrowserSession,
        context: BrowserContext,
        page: Page,
    ) -> None:
        self.session = session
        self.context = context
        self.page = page
        self.last_observation: BrowserObservation | None = None


class BrowserRuntime:
    """Owns Chromium and one isolated BrowserContext per governed session."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: dict[UUID, _SessionHandle] = {}
        self._launch_lock = asyncio.Lock()

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

    async def create_session(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        ttl_seconds: int = 1800,
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
            self._sessions[session_id] = _SessionHandle(
                session=session,
                context=context,
                page=page,
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
        observation = await observe_page(handle.page, session_id=session_id)
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
        if operation == "navigation.open":
            if not url:
                raise ValueError("URL is required for browser navigation.")
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
            await self._locator(handle, locator).click(timeout=timeout_ms)
            await handle.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        else:
            raise ValueError("Unsupported browser navigation operation.")
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
