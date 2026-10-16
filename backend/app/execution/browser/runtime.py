from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import os
import signal
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import quote_plus, unquote_plus
from uuid import UUID, uuid4

from playwright.async_api import (
    APIResponse,
    Browser,
    BrowserContext,
    Dialog,
    Frame,
    Locator,
    Page,
    Playwright,
    Request,
    Route,
    WebSocketRoute,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from redis.exceptions import RedisError

from app.execution.browser.contracts import (
    BrowserDownload,
    BrowserLocator,
    BrowserObservation,
    BrowserSession,
    BrowserVerificationProbe,
)
from app.execution.browser.credentials import (
    LOGIN_IN_ADDRESS,
    BrowserAuthenticationFailure,
    BrowserCredentialBundle,
)
from app.execution.browser.egress import (
    BrowserEgressDecision,
    evaluate_browser_egress,
    resource_refusal,
)
from app.execution.browser.errors import (
    BrowserCapacityUnavailable,
    BrowserDetachedFrame,
    BrowserDownloadFailure,
    BrowserElementNotFound,
    BrowserOwnershipLost,
    BrowserRuntimeLimitExceeded,
    BrowserStaleObservation,
)
from app.execution.browser.keyboard import KEYBOARD_METADATA_SCRIPT, read_only_key
from app.execution.browser.memory import browser_memory_snapshot, chromium_process_ids
from app.execution.browser.observation import observe_page, origin_for_url
from app.execution.browser.policy import (
    BrowserDomainPolicy,
    BrowserNavigationBlocked,
    BrowserNavigationDecision,
)
from app.execution.browser.sensitive import is_sensitive_field_metadata
from app.execution.browser.verification import BrowserVerificationExpectation
from app.execution.redaction import redact_sensitive_structure, redact_url
from app.infrastructure.redis.coordination import Lease, RedisCoordinator

logger = logging.getLogger(__name__)

# How long a submitted form has to start a page request before it counts as same-page. A request
# starts within milliseconds of a submit, however slowly the site then answers.
SUBMIT_NAVIGATION_WINDOW_MS = 1_500
# After an action, how long the page may take to finish what the action started (its requests, a
# page change sent by script, content drawn after load) before it is observed as it stands (F48).
ACTION_SETTLE_MAX_SECONDS = 5.0
# The page has settled once its content is unchanged, and no request the action started is still
# open, for this long.
ACTION_QUIET_SECONDS = 0.4
_SETTLE_POLL_SECONDS = 0.1
# Requests that bring the page or its data; images, fonts and media never hold an observation.
_SETTLE_REQUEST_TYPES = frozenset({"document", "xhr", "fetch"})
_ELEMENT_OPERATIONS = frozenset(
    {
        "element.click",
        "element.type",
        "element.clear",
        "element.select",
        "element.check",
        "element.uncheck",
        "element.press_key",
        "element.hover",
    }
)
# Scroll the first scrollable container in the section under the heading that names it.
_SECTION_SCROLL_SCRIPT = """({query, axis, amount}) => {
    const heading = Array.from(document.querySelectorAll(
      'h1,h2,h3,h4,h5,h6,[role="heading"]'
    )).find(el => (el.innerText || '').trim().toLowerCase()
      .includes(query.trim().toLowerCase()));
    const section = heading?.closest('section,[role="region"],article')
      || heading?.parentElement;
    if (!section) return false;
    const container = [section, ...section.querySelectorAll('*')]
      .find(el => {
        const style = getComputedStyle(el);
        const overflow = axis === 'horizontal'
          ? style.overflowX : style.overflowY;
        return ['auto', 'scroll', 'hidden'].includes(overflow)
          && (axis === 'horizontal'
            ? el.scrollWidth > el.clientWidth + 2
            : el.scrollHeight > el.clientHeight + 2);
      });
    if (!container) return false;
    container.scrollBy({
      left: axis === 'horizontal' ? amount : 0,
      top: axis === 'vertical' ? amount : 0
    });
    return true;
  }"""
# A cheap measure of the page's content (no layout); null while the page is still loading.
_PAGE_SIGNATURE_SCRIPT = (
    "() => document.readyState === 'loading' ? null : "
    "[document.body ? document.body.textContent.trim().length : 0, "
    "document.getElementsByTagName('*').length]"
)


def _carries_secret(url: str, secrets: set[str]) -> bool:
    """Whether a web address carries a secret, as typed or encoded. Very short values are skipped,
    as they would match ordinary addresses."""
    decoded = unquote_plus(url)
    return any(
        len(secret) >= 4 and (secret in decoded or quote_plus(secret) in url) for secret in secrets
    )


def _main_frame_navigation(request: Request) -> bool:
    try:
        return request.is_navigation_request() and request.frame.parent_frame is None
    except PlaywrightError:  # a popup's first request can come before its frame exists
        return False


class _SubmitWatch:
    """What a form submit did to the page (F38): whether it started opening a new page, and when
    that page committed or failed."""

    def __init__(self, page: Page, timeout_ms: int) -> None:
        self.started = asyncio.ensure_future(
            page.wait_for_event(
                "request", predicate=_main_frame_navigation, timeout=SUBMIT_NAVIGATION_WINDOW_MS
            )
        )
        self.committed = asyncio.ensure_future(
            page.wait_for_event(
                "framenavigated",
                predicate=lambda frame: frame.parent_frame is None,
                timeout=timeout_ms,
            )
        )
        self.failed = asyncio.ensure_future(
            page.wait_for_event(
                "requestfailed", predicate=_main_frame_navigation, timeout=timeout_ms
            )
        )

    @classmethod
    def begin(cls, page: Page, timeout_ms: int) -> _SubmitWatch:
        return cls(page, timeout_ms)

    async def settle(self, page: Page, timeout_ms: int) -> None:
        """Wait for the page a submit opened to load; a same-page form has nothing to wait for. A
        slow or failed page is observed as it stands."""
        try:
            await self.started
        except PlaywrightError:
            return
        await asyncio.wait({self.committed, self.failed}, return_when=asyncio.FIRST_COMPLETED)
        if self.committed.done() and self.committed.exception() is None:
            with contextlib.suppress(PlaywrightError):
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

    def close(self) -> None:
        for waiter in (self.started, self.committed, self.failed):
            if not waiter.done():
                waiter.cancel()
            elif not waiter.cancelled():
                waiter.exception()  # retrieved, so an expected timeout is never logged as lost


telemetry_logger = logging.getLogger("uvicorn.error")


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

    async def fail(self, session_id: UUID) -> BrowserSession: ...

    async def observe(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        focus_text: str | None = None,
    ) -> BrowserObservation: ...

    async def current_observation(
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
        axis: str = "vertical",
        focus_text: str | None = None,
        dialog_action: str | None = None,
        prompt_text: str | None = None,
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
        dialog_action: str | None = None,
        prompt_text: str | None = None,
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

    async def screenshot(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> bytes: ...

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
    ) -> BrowserObservation: ...

    async def download_file(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        locator: BrowserLocator,
        timeout_ms: int = 30_000,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> tuple[BrowserDownload, BrowserObservation]: ...

    async def verify_browser_state(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        expectation: BrowserVerificationExpectation,
        result_output: dict[str, object],
    ) -> BrowserVerificationProbe: ...

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
        max_pages: int,
        capacity_lease: Lease | None = None,
        organization_lease: Lease | None = None,
        run_lease: Lease | None = None,
        fencing_token: int | None = None,
        idle_ttl_seconds: int = 900,
    ) -> None:
        self.session = session
        self.context = context
        self.page = page
        self.navigation_policy = navigation_policy
        self.max_pages = max_pages
        self.capacity_lease = capacity_lease
        self.organization_lease = organization_lease
        self.run_lease = run_lease
        self.fencing_token = fencing_token
        self.idle_ttl_seconds = idle_ttl_seconds
        self.limit_error: str | None = None
        self.last_observation: BrowserObservation | None = None
        self.transition_trail: list[BrowserNavigationDecision] = []
        self.blocked_navigation: BrowserNavigationDecision | None = None
        self.blocked_popup_initial_navigation = False
        self.sensitive_values: set[str] = set()
        self.pages: dict[str, Page] = {}
        self.dialog_events: list[dict[str, object]] = []
        self.dialog_action: str | None = None
        self.dialog_prompt_text: str | None = None
        self.keyboard_read_only_until = 0.0
        self.keyboard_write_blocked = False
        # A page's own requests: each host's answer, and how many were refused (F39).
        self.remembered_hosts: dict[str, BrowserEgressDecision] = {}
        self.refused_requests = 0
        # Secrets typed into a login, which no request may carry in its web address (F45).
        self.login_secrets: set[str] = set()
        # Each page's open page and data requests, with when they started (F48).
        self.open_requests: dict[Page, dict[Request, float]] = {}


class BrowserRuntime:
    """Owns Chromium and one isolated BrowserContext per governed session."""

    def __init__(
        self,
        *,
        headless: bool = True,
        coordinator: RedisCoordinator | None = None,
        idle_shutdown_seconds: int = 180,
        max_active_sessions: int = 1,
        max_sessions_per_organization: int = 1,
        memory_soft_limit_percent: int = 99,
        memory_hard_limit_percent: int = 100,
        max_pages_per_session: int = 3,
        launch_min_headroom_bytes: int = 0,
    ) -> None:
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: dict[UUID, _SessionHandle] = {}
        self._launch_lock = asyncio.Lock()
        self._capacity_lock = asyncio.Lock()
        self._coordinator = coordinator
        self._idle_shutdown_seconds = max(1, idle_shutdown_seconds)
        self._max_active_sessions = max(1, max_active_sessions)
        self._max_sessions_per_organization = max(1, max_sessions_per_organization)
        self._memory_soft_limit_percent = max(1, min(memory_soft_limit_percent, 99))
        self._memory_hard_limit_percent = max(
            self._memory_soft_limit_percent,
            min(memory_hard_limit_percent, 100),
        )
        self._max_pages_per_session = max(1, min(max_pages_per_session, 3))
        self._launch_min_headroom_bytes = max(0, launch_min_headroom_bytes)
        self._idle_shutdown_task: asyncio.Task[None] | None = None

    def _memory_snapshot(self):
        return browser_memory_snapshot(
            active_sessions=len(self._sessions),
            open_pages=sum(
                len([page for page in handle.pages.values() if not page.is_closed()])
                for handle in self._sessions.values()
            ),
        )

    def _log_memory(self, phase: str, *, screenshot_bytes: int | None = None) -> None:
        snapshot = self._memory_snapshot()
        telemetry_logger.info(
            "Browser memory phase=%s python=%s chromium=%s renderer=%s cgroup=%s limit=%s "
            "percent=%s sessions=%s pages=%s screenshot_bytes=%s",
            phase,
            snapshot.python_rss_bytes,
            snapshot.chromium_rss_bytes,
            snapshot.renderer_rss_bytes,
            snapshot.cgroup_current_bytes,
            snapshot.cgroup_limit_bytes,
            round(snapshot.cgroup_percent, 2) if snapshot.cgroup_percent is not None else None,
            snapshot.active_sessions,
            snapshot.open_pages,
            screenshot_bytes,
        )

    def _cancel_idle_shutdown(self) -> None:
        task = self._idle_shutdown_task
        if task is not None and not task.done():
            task.cancel()
        self._idle_shutdown_task = None

    def _schedule_idle_shutdown(self) -> None:
        if self._sessions or (self._browser is None and self._playwright is None):
            return
        self._cancel_idle_shutdown()
        self._idle_shutdown_task = asyncio.create_task(self._idle_shutdown())

    async def _idle_shutdown(self) -> None:
        try:
            await asyncio.sleep(self._idle_shutdown_seconds)
            if self._sessions:
                return
            await self._shutdown_browser_engine()
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._idle_shutdown_task is current:
                self._idle_shutdown_task = None

    async def _reap_orphaned_chromium(
        self,
        *,
        baseline_pids: set[int] | None = None,
    ) -> None:
        if self._sessions:
            return
        current = chromium_process_ids()
        targets = current if baseline_pids is None else current - baseline_pids
        if not targets:
            return

        telemetry_logger.warning(
            "Reaping orphaned Chromium processes pids=%s",
            ",".join(str(pid) for pid in sorted(targets)),
        )
        for pid in targets:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue

        await asyncio.sleep(0.75)
        survivors = chromium_process_ids() & targets
        force_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        for pid in survivors:
            try:
                os.kill(pid, force_signal)
            except (ProcessLookupError, PermissionError):
                continue

        if survivors:
            await asyncio.sleep(0.25)
        remaining = chromium_process_ids() & targets
        telemetry_logger.info(
            "Chromium orphan reap completed requested=%s survivors=%s",
            len(targets),
            len(remaining),
        )

    async def _shutdown_browser_engine(self) -> None:
        async with self._launch_lock:
            if self._sessions:
                return
            if self._browser is not None:
                try:
                    await self._browser.close()
                except PlaywrightError as error:
                    logger.warning("Failed to close idle Chromium browser cleanly: %s", error)
                finally:
                    self._browser = None
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except PlaywrightError as error:
                    logger.warning("Failed to stop idle Playwright runtime cleanly: %s", error)
                finally:
                    self._playwright = None
            await self._reap_orphaned_chromium()
        self._log_memory("after_chromium_shutdown")

    async def shutdown_if_idle(self) -> None:
        self._cancel_idle_shutdown()
        if self._sessions:
            return
        await self._shutdown_browser_engine()

    async def _release_capacity(self, handle: _SessionHandle) -> None:
        if self._coordinator is None:
            return
        for name in ("run_lease", "organization_lease", "capacity_lease"):
            lease = getattr(handle, name)
            if lease is None:
                continue
            try:
                await self._coordinator.release_lock(lease)
            except RedisError as error:
                logger.warning("Failed to release browser %s: %s", name, error)
            finally:
                setattr(handle, name, None)

    async def _acquire_slot(self, key: str, limit: int, ttl_seconds: int) -> Lease | None:
        if self._coordinator is None:
            return None
        for index in range(limit):
            lease = await self._coordinator.acquire_lock(f"{key}:{index}", ttl_seconds=ttl_seconds)
            if lease is not None:
                return lease
        return None

    @staticmethod
    def _redact_metadata(value: object, sensitive_values: set[str]) -> object:
        if isinstance(value, str):
            redacted = redact_url(value)
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
        self._cancel_idle_shutdown()
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        async with self._launch_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            snapshot = self._memory_snapshot()
            if (
                self._launch_min_headroom_bytes > 0
                and snapshot.cgroup_current_bytes is not None
                and snapshot.cgroup_limit_bytes is not None
            ):
                headroom = max(
                    0,
                    snapshot.cgroup_limit_bytes - snapshot.cgroup_current_bytes,
                )
                if headroom < self._launch_min_headroom_bytes:
                    telemetry_logger.info(
                        "Browser launch deferred cgroup=%s limit=%s headroom=%s required=%s "
                        "percent=%s",
                        snapshot.cgroup_current_bytes,
                        snapshot.cgroup_limit_bytes,
                        headroom,
                        self._launch_min_headroom_bytes,
                        round(snapshot.cgroup_percent, 2)
                        if snapshot.cgroup_percent is not None
                        else None,
                    )
                    raise BrowserCapacityUnavailable(
                        "Governed browser launch is deferred until the service has "
                        "enough memory headroom."
                    )
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._log_memory("before_browser_launch")
            launch_started = time.monotonic()
            baseline_chromium_pids = chromium_process_ids()
            launch_args = [
                "--renderer-process-limit=1",
                "--disable-gpu",
                "--disable-dev-shm-usage",
            ]
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=self._headless,
                    args=launch_args,
                )
            except PlaywrightError as error:
                if "Executable doesn't exist" not in str(error):
                    await self._reap_orphaned_chromium(baseline_pids=baseline_chromium_pids)
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
                try:
                    _, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=240,
                    )
                except TimeoutError as timeout_error:
                    process.kill()
                    await process.communicate()
                    raise RuntimeError(
                        "Timed out while provisioning the Chromium browser runtime."
                    ) from timeout_error
                if process.returncode != 0:
                    raise RuntimeError(
                        "Failed to provision the Chromium browser runtime: "
                        + stderr.decode("utf-8", errors="replace")[-1000:]
                    ) from error
                try:
                    self._browser = await self._playwright.chromium.launch(
                        headless=self._headless,
                        args=launch_args,
                    )
                except BaseException:
                    await self._reap_orphaned_chromium(baseline_pids=baseline_chromium_pids)
                    raise
            except BaseException:
                await self._reap_orphaned_chromium(baseline_pids=baseline_chromium_pids)
                raise
            telemetry_logger.info(
                "Browser launch completed elapsed=%.3fs",
                time.monotonic() - launch_started,
            )
            self._log_memory("after_browser_launch")
            return self._browser

    async def health(self) -> bool:
        # Health checks must not wake Chromium. The runtime is considered lazily
        # available while stopped; the first governed action performs the real launch.
        return self._browser is None or self._browser.is_connected()

    async def prepare(self) -> None:
        try:
            await self._ensure_browser()
        except BaseException:
            try:
                await asyncio.shield(self.shutdown_if_idle())
            except (PlaywrightError, RuntimeError):
                logger.warning("Failed to reclaim browser runtime after cold-start failure.")
            raise

    async def optional_evidence_allowed(self) -> bool:
        snapshot = self._memory_snapshot()
        percent = snapshot.cgroup_percent
        return percent is None or percent < self._memory_soft_limit_percent

    async def reap_expired(self) -> int:
        now = datetime.now(UTC)
        expired = [
            session_id
            for session_id, handle in self._sessions.items()
            if handle.session.expires_at <= now
        ]
        for session_id in expired:
            try:
                await self.expire(session_id)
            except (LookupError, PlaywrightError):
                continue
        if expired:
            self._log_memory("after_expired_session_reap")
        return len(expired)

    def _register_page(self, handle: _SessionHandle, page: Page) -> None:
        if page in handle.pages.values():
            handle.page = page
            return
        if len(handle.pages) >= handle.max_pages:
            handle.limit_error = (
                f"Browser page limit exceeded ({handle.max_pages} pages per session)."
            )
            asyncio.create_task(page.close())
            return
        page_id = f"p{len(handle.pages) + 1}"
        handle.pages[page_id] = page
        handle.page = page
        page.on(
            "dialog",
            lambda dialog: asyncio.create_task(self._handle_dialog(handle, dialog)),
        )
        page.on("close", lambda _page: self._page_closed(handle, page))
        opened = handle.open_requests.setdefault(page, {})

        def request_started(request: Request) -> None:
            if request.resource_type in _SETTLE_REQUEST_TYPES:
                opened[request] = time.monotonic()

        def request_ended(request: Request) -> None:
            opened.pop(request, None)

        # A request ends when its answer arrives: a script that got its answer may move to another
        # page without reading the body, and that request then never finishes. What the page draws
        # from the answer afterwards is waited for by watching the page itself.
        page.on("request", request_started)
        page.on("response", lambda response: request_ended(response.request))
        page.on("requestfailed", request_ended)

    @staticmethod
    def _page_closed(handle: _SessionHandle, page: Page) -> None:
        handle.open_requests.pop(page, None)
        for key, candidate in tuple(handle.pages.items()):
            if candidate is page:
                handle.pages.pop(key, None)
        if handle.page is page:
            remaining = [
                candidate for candidate in handle.pages.values() if not candidate.is_closed()
            ]
            if remaining:
                handle.page = remaining[-1]

    async def _handle_dialog(self, handle: _SessionHandle, dialog: Dialog) -> None:
        action = handle.dialog_action
        prompt_text = handle.dialog_prompt_text
        decision = "dismissed"
        if action == "accept":
            await dialog.accept(prompt_text or "")
            decision = "accepted"
        else:
            await dialog.dismiss()
        event = {
            "type": dialog.type,
            "message": dialog.message,
            "defaultValue": dialog.default_value,
            "decision": decision,
        }
        redacted = self._redact_metadata(event, handle.sensitive_values)
        if isinstance(redacted, dict):
            handle.dialog_events.append(redacted)
            del handle.dialog_events[:-20]
        handle.dialog_action = None
        handle.dialog_prompt_text = None

    @staticmethod
    def _set_dialog_policy(
        handle: _SessionHandle,
        *,
        action: str | None,
        prompt_text: str | None,
    ) -> None:
        if action not in {None, "accept", "dismiss"}:
            raise ValueError("Browser dialog action must be accept or dismiss.")
        handle.dialog_action = action
        handle.dialog_prompt_text = prompt_text
        handle.keyboard_read_only_until = 0.0
        handle.keyboard_write_blocked = False

    @staticmethod
    def _raise_if_limit(handle: _SessionHandle) -> None:
        if handle.limit_error is not None:
            message = handle.limit_error
            handle.limit_error = None
            raise BrowserRuntimeLimitExceeded(message)

    async def _route_web_socket(self, handle: _SessionHandle, socket: WebSocketRoute) -> None:
        """A page's WebSocket follows the same rule as its other requests (F39)."""
        url = socket.url
        refusal = await resource_refusal(
            "http" + url[2:] if url.startswith("ws") else url,
            denied_origins=handle.navigation_policy.denied_origins
            if handle.navigation_policy is not None
            else (),
            allow_private_network=handle.navigation_policy is not None
            and handle.navigation_policy.allow_private_network,
            remembered=handle.remembered_hosts,
        )
        if refusal is None:
            socket.connect_to_server()
            return
        handle.refused_requests += 1
        logger.info(
            "Browser request refused session=%s type=websocket url=%s reason=%s",
            handle.session.id,
            redact_url(url),
            refusal,
        )
        await socket.close(code=1008, reason="Refused by the connection's rules.")

    async def _route_request(
        self,
        handle: _SessionHandle,
        route: Route,
        request: Request,
    ) -> None:
        if time.monotonic() < handle.keyboard_read_only_until and request.method not in {
            "GET",
            "HEAD",
            "OPTIONS",
        }:
            handle.keyboard_write_blocked = True
            await route.abort("blockedbyclient")
            return
        if handle.login_secrets and _carries_secret(request.url, handle.login_secrets):
            await self._refuse_login_in_address(handle, route, request)
            return
        if not request.is_navigation_request():
            await self._route_page_request(handle, route, request)
            return

        source_url, frame_unavailable = self._navigation_source(handle, request)
        decision = await self._governed_decision(handle, request.url, source_url)
        # A popup's initial navigation can arrive before its Frame exists. Treat it as top-level
        # so a denied popup is surfaced to the governed action instead of becoming a silent
        # side channel.
        top_level = frame_unavailable or request.frame == request.frame.page.main_frame
        if not await self._admit(handle, route, decision, top_level, frame_unavailable):
            return
        response = await self._fetch_navigation(handle, route, request)
        if response is None:
            return
        location = response.headers.get("location") if 300 <= response.status < 400 else None
        if location:
            redirect = await self._governed_decision(handle, location, request.url)
            if not await self._admit(handle, route, redirect, top_level, frame_unavailable):
                return
        await route.fulfill(response=response)

    @staticmethod
    async def _refuse_login_in_address(
        handle: _SessionHandle, route: Route, request: Request
    ) -> None:
        """A request that would put a typed secret in a web address is never sent (F45)."""
        handle.refused_requests += 1
        logger.warning(
            "Browser request refused session=%s: a login secret was in its web address",
            handle.session.id,
        )
        if request.is_navigation_request():
            decision = BrowserNavigationDecision(
                allowed=False,
                source_url=None,
                target_url=redact_url(request.url),
                target_origin=origin_for_url(request.url),
                reason=LOGIN_IN_ADDRESS,
                cross_origin=False,
            )
            handle.transition_trail.append(decision)
            handle.blocked_navigation = decision
        await route.abort("blockedbyclient")

    async def _route_page_request(
        self, handle: _SessionHandle, route: Route, request: Request
    ) -> None:
        """A page's own request: the RAM rule first, then blocked sites and private addresses."""
        policy = handle.navigation_policy
        if (
            policy is not None
            and not policy.load_visual_resources
            and request.resource_type in {"image", "media", "font"}
        ):
            await route.abort("blockedbyclient")
            return
        refusal = await resource_refusal(
            request.url,
            denied_origins=policy.denied_origins if policy is not None else (),
            allow_private_network=policy is not None and policy.allow_private_network,
            remembered=handle.remembered_hosts,
        )
        if refusal is not None:
            handle.refused_requests += 1
            logger.info(
                "Browser request refused session=%s type=%s url=%s reason=%s",
                handle.session.id,
                request.resource_type,
                redact_url(request.url),
                refusal,
            )
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    @staticmethod
    def _navigation_source(handle: _SessionHandle, request: Request) -> tuple[str | None, bool]:
        """The page a navigation starts from, and whether its frame did not exist yet."""
        try:
            source_url = request.frame.url or None
        except PlaywrightError:
            # A popup's initial navigation request can arrive before Chromium has
            # created its Frame object. The destination must still pass policy.
            current_url = handle.page.url
            return (current_url if current_url not in {"", "about:blank"} else None), True
        if source_url == "about:blank":
            source_url = handle.page.url if handle.page.url != "about:blank" else None
        return source_url, False

    @staticmethod
    async def _governed_decision(
        handle: _SessionHandle, target_url: str, source_url: str | None
    ) -> BrowserNavigationDecision:
        """The connection's site rules, then its private-network rule, for a page opening."""
        policy = handle.navigation_policy
        if policy is None:
            return BrowserNavigationDecision(
                allowed=False,
                source_url=source_url,
                target_url=target_url,
                target_origin=origin_for_url(target_url),
                reason="Browser session has no authorized destination policy.",
                cross_origin=False,
            )
        decision = policy.permits(target_url, source_url=source_url)
        if not decision.allowed:
            return decision
        egress = await evaluate_browser_egress(
            decision.target_url,
            allow_private_network=policy.allow_private_network,
        )
        if egress.allowed:
            return decision
        return BrowserNavigationDecision(
            allowed=False,
            source_url=source_url,
            target_url=decision.target_url,
            target_origin=decision.target_origin,
            reason=egress.reason,
            cross_origin=decision.cross_origin,
        )

    @staticmethod
    async def _admit(
        handle: _SessionHandle,
        route: Route,
        decision: BrowserNavigationDecision,
        top_level: bool,
        frame_unavailable: bool,
    ) -> bool:
        """Record the decision; a refused one is aborted (and surfaced when top-level)."""
        handle.transition_trail.append(decision)
        del handle.transition_trail[:-50]
        if decision.allowed:
            return True
        if top_level:
            handle.blocked_navigation = decision
            handle.blocked_popup_initial_navigation = frame_unavailable
        await route.abort("blockedbyclient")
        return False

    @staticmethod
    async def _fetch_navigation(
        handle: _SessionHandle, route: Route, request: Request
    ) -> APIResponse | None:
        """Fetch an admitted page without following redirects, so each one is checked."""
        fetch_started = time.monotonic()
        telemetry_logger.info(
            "Browser navigation fetch started run=%s session=%s url=%s",
            handle.session.run_id,
            handle.session.id,
            redact_url(request.url),
        )
        try:
            response = await route.fetch(
                max_redirects=0,
                timeout=20_000,
            )
        except PlaywrightError as error:
            logger.warning(
                "Browser navigation fetch failed run=%s session=%s elapsed=%.3fs error=%s",
                handle.session.run_id,
                handle.session.id,
                time.monotonic() - fetch_started,
                type(error).__name__,
            )
            await route.abort("timedout")
            return None
        telemetry_logger.info(
            "Browser navigation fetch completed run=%s session=%s status=%s elapsed=%.3fs",
            handle.session.run_id,
            handle.session.id,
            response.status,
            time.monotonic() - fetch_started,
        )
        return response

    def _check_local_capacity(self, organization_id: UUID) -> None:
        """Memory pressure and this server's session limits, before any lease is taken."""
        current_memory = self._memory_snapshot()
        if (
            current_memory.cgroup_percent is not None
            and current_memory.cgroup_percent >= self._memory_soft_limit_percent
        ):
            raise BrowserCapacityUnavailable("Browser capacity is paused by memory pressure.")
        if len(self._sessions) >= self._max_active_sessions:
            raise BrowserCapacityUnavailable("Waiting for browser capacity.")
        if (
            sum(
                handle.session.organization_id == organization_id
                for handle in self._sessions.values()
            )
            >= self._max_sessions_per_organization
        ):
            raise BrowserCapacityUnavailable("Organization browser capacity is in use.")

    async def create_session(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        ttl_seconds: int = 1800,
        navigation_policy: BrowserDomainPolicy | None = None,
    ) -> BrowserSession:
        async with self._capacity_lock:
            self._check_local_capacity(organization_id)
            lease_ttl = max(120, min(ttl_seconds, 900) + 120)
            capacity_lease: Lease | None = None
            organization_lease: Lease | None = None
            run_lease: Lease | None = None
            fencing_token: int | None = None
            acquired: list[Lease] = []
            try:
                if self._coordinator is not None:
                    if run_id is not None:
                        run_lease = await self._coordinator.acquire_lock(
                            f"browser-run:{run_id}", ttl_seconds=lease_ttl
                        )
                        if run_lease is None:
                            raise BrowserCapacityUnavailable(
                                "This run already owns a browser session."
                            )
                        acquired.append(run_lease)
                        fencing_token = await self._coordinator.next_fencing_token(
                            f"browser-run:{run_id}"
                        )
                    organization_lease = await self._acquire_slot(
                        f"browser-org:{organization_id}",
                        self._max_sessions_per_organization,
                        lease_ttl,
                    )
                    if organization_lease is None:
                        raise BrowserCapacityUnavailable("Organization browser capacity is in use.")
                    acquired.append(organization_lease)
                    capacity_lease = await self._acquire_slot(
                        "browser-capacity", self._max_active_sessions, lease_ttl
                    )
                    if capacity_lease is None:
                        raise BrowserCapacityUnavailable("Waiting for browser capacity.")
                    acquired.append(capacity_lease)

                self._cancel_idle_shutdown()
                context: BrowserContext | None = None
                session_id: UUID | None = None
                try:
                    browser = await self._ensure_browser()
                    context = await browser.new_context(
                        service_workers="block",
                        viewport={"width": 1280, "height": 720},
                        device_scale_factor=1,
                    )
                    page = await context.new_page()
                    now = datetime.now(UTC)
                    session_id = uuid4()
                    idle_ttl_seconds = max(60, min(ttl_seconds, 900))
                    session = BrowserSession(
                        id=session_id,
                        organization_id=organization_id,
                        worker_id=worker_id,
                        run_id=run_id,
                        browser_context_id=f"ctx_{uuid4().hex}",
                        created_at=now,
                        expires_at=now + timedelta(seconds=idle_ttl_seconds),
                        status="active",
                    )
                    handle = _SessionHandle(
                        session=session,
                        context=context,
                        page=page,
                        navigation_policy=navigation_policy,
                        max_pages=self._max_pages_per_session,
                        capacity_lease=capacity_lease,
                        organization_lease=organization_lease,
                        run_lease=run_lease,
                        fencing_token=fencing_token,
                        idle_ttl_seconds=idle_ttl_seconds,
                    )
                    self._register_page(handle, page)
                    context.on("page", lambda popup: self._register_page(handle, popup))
                    self._sessions[session_id] = handle
                    await context.route(
                        "**/*",
                        lambda route, request: self._route_request(handle, route, request),
                    )
                    await context.route_web_socket(
                        "**/*", lambda socket: self._route_web_socket(handle, socket)
                    )
                    self._log_memory("after_browser_context")
                    return session
                except BaseException:
                    if context is not None:
                        await context.close()
                    if session_id is not None:
                        self._sessions.pop(session_id, None)
                    if not self._sessions:
                        self._schedule_idle_shutdown()
                    raise
            except BaseException:
                if self._coordinator is not None:
                    for lease in reversed(acquired):
                        try:
                            await self._coordinator.release_lock(lease)
                        except RedisError as error:
                            logger.warning("Failed to release browser lease: %s", error)
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
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        # An active run gets a fresh idle window on every continuation.
        handle.session = replace(
            handle.session,
            expires_at=datetime.now(UTC) + timedelta(seconds=handle.idle_ttl_seconds),
        )
        if self._coordinator is not None:
            if handle.run_lease is not None and handle.fencing_token is not None:
                generation = await self._coordinator.current_fencing_token(handle.run_lease.key)
                if generation != handle.fencing_token:
                    await self.fail(session_id)
                    raise BrowserOwnershipLost("A newer browser owner has taken over this run.")
            remaining = max(
                120,
                int((handle.session.expires_at - datetime.now(UTC)).total_seconds()) + 120,
            )
            for lease in (handle.run_lease, handle.organization_lease, handle.capacity_lease):
                if lease is not None and not await self._coordinator.renew_lock(
                    lease, ttl_seconds=remaining
                ):
                    await self.fail(session_id)
                    raise BrowserOwnershipLost(
                        "Browser ownership expired before the session completed."
                    )
        return handle.session

    async def _finish(self, session_id: UUID, status: str) -> BrowserSession:
        handle = self._sessions.pop(session_id, None)
        if handle is None:
            raise LookupError("Browser session does not exist.")
        current_url = handle.page.url
        try:
            await handle.context.close()
        finally:
            handle.session = replace(
                handle.session,
                status=status,  # type: ignore[arg-type]
                current_url=current_url,
                current_origin=origin_for_url(current_url),
            )
            handle.sensitive_values.clear()
            await self._release_capacity(handle)
            self._log_memory("after_browser_context_close")
            if not self._sessions:
                self._schedule_idle_shutdown()
        return handle.session

    async def close(self, session_id: UUID) -> BrowserSession:
        return await self._finish(session_id, "closed")

    async def expire(self, session_id: UUID) -> BrowserSession:
        return await self._finish(session_id, "expired")

    async def terminate(self, session_id: UUID) -> BrowserSession:
        return await self._finish(session_id, "terminated")

    async def fail(self, session_id: UUID) -> BrowserSession:
        return await self._finish(session_id, status="failed")

    async def shutdown(self) -> None:
        self._cancel_idle_shutdown()
        for session_id in tuple(self._sessions):
            try:
                await self.terminate(session_id)
            except (LookupError, PlaywrightError) as error:
                logger.warning("Failed to terminate browser session %s: %s", session_id, error)
        self._cancel_idle_shutdown()
        await self._shutdown_browser_engine()
        if self._coordinator is not None:
            await self._coordinator.close()

    async def current_observation(
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
        if handle.last_observation is not None:
            return handle.last_observation
        return await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )

    async def observe(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        focus_text: str | None = None,
    ) -> BrowserObservation:
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        snapshot = self._memory_snapshot()
        pressure = snapshot.cgroup_percent
        under_soft_pressure = pressure is not None and pressure >= self._memory_soft_limit_percent
        under_hard_pressure = pressure is not None and pressure >= self._memory_hard_limit_percent
        if under_hard_pressure:
            logger.warning(
                "Browser memory hard threshold reached percent=%.2f session=%s; "
                "closing inactive pages and suppressing optional snapshots.",
                pressure,
                session_id,
            )
            for page in tuple(handle.pages.values()):
                if page is handle.page or page.is_closed():
                    continue
                await page.close()
        observation = await observe_page(
            handle.page,
            session_id=session_id,
            sensitive_values=tuple(handle.sensitive_values),
            include_aria_snapshot=not under_soft_pressure,
            include_dom_snapshot=(
                handle.navigation_policy is not None
                and handle.navigation_policy.capture_dom_snapshot
                and not under_soft_pressure
            ),
            focus_text=focus_text,
        )
        page_state = {
            **observation.page_state,
            "keyboardWriteBlocked": handle.keyboard_write_blocked,
            "refusedPageRequests": handle.refused_requests,
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
            "pages": [
                {
                    "id": page_id,
                    "url": self._redact_metadata(page.url, handle.sensitive_values),
                    "active": page is handle.page,
                }
                for page_id, page in handle.pages.items()
                if not page.is_closed()
            ],
            "dialogs": list(handle.dialog_events[-10:]),
        }
        observation = replace(observation, page_state=page_state)
        handle.last_observation = observation
        self._log_memory("after_observation")
        handle.session = replace(
            handle.session,
            current_url=observation.url,
            current_origin=origin_for_url(observation.url),
        )
        return observation

    def _frame_for_locator(
        self,
        handle: _SessionHandle,
        locator: BrowserLocator,
    ) -> Page | Frame:
        if locator.frame_name is None and locator.frame_origin is None:
            return handle.page

        policy = handle.navigation_policy
        if policy is None:
            raise PermissionError("Cross-frame interaction requires destination policy.")

        requested_origin = (
            origin_for_url(locator.frame_origin) if locator.frame_origin is not None else None
        )
        if locator.frame_origin is not None and requested_origin is None:
            raise ValueError("Browser frameOrigin must be an absolute HTTP or HTTPS origin.")
        if requested_origin is not None and (
            requested_origin in policy.denied_origins
            or requested_origin not in policy.allowed_origins
        ):
            source_origin = origin_for_url(handle.page.url)
            raise BrowserNavigationBlocked(
                BrowserNavigationDecision(
                    allowed=False,
                    source_url=handle.page.url,
                    target_url=locator.frame_origin or requested_origin,
                    target_origin=requested_origin,
                    reason=(
                        "Browser destination origin is explicitly denied."
                        if requested_origin in policy.denied_origins
                        else "Browser destination origin is not authorized for this resource."
                    ),
                    cross_origin=(source_origin is not None and source_origin != requested_origin),
                )
            )

        selected: Frame | None = None
        for frame in handle.page.frames:
            if locator.frame_name is not None and frame.name != locator.frame_name:
                continue
            frame_origin = origin_for_url(frame.url)
            if locator.frame_origin is not None and frame_origin != locator.frame_origin:
                continue
            selected = frame
            break
        if selected is None:
            raise BrowserDetachedFrame("Browser frame target was not found.")

        decision = policy.permits(selected.url, source_url=handle.page.url)
        if not decision.allowed:
            raise BrowserNavigationBlocked(decision)
        return selected

    def _locator(self, handle: _SessionHandle, locator: BrowserLocator) -> Locator:
        root = self._frame_for_locator(handle, locator)
        if locator.strategy == "role":
            return root.get_by_role(
                locator.value,  # type: ignore[arg-type]
                name=locator.name,
                exact=locator.exact,
            )
        if locator.strategy == "label":
            return root.get_by_label(locator.value, exact=locator.exact)
        if locator.strategy == "text":
            return root.get_by_text(locator.value, exact=locator.exact)
        if locator.strategy == "css":
            return root.locator(locator.value)
        if locator.strategy == "observation_ref":
            if isinstance(root, Frame):
                raise LookupError(
                    "Observation references are main-frame scoped; use a semantic locator for iframe interaction."
                )
            observation = handle.last_observation
            if observation is None:
                raise LookupError("No browser observation is available for this session.")
            if locator.observation_id and locator.observation_id != observation.id:
                raise BrowserStaleObservation("Browser locator refers to a stale observation.")
            refs = {item.ref for item in observation.elements}
            if locator.value not in refs:
                raise BrowserElementNotFound("Browser observation element reference was not found.")
            if not locator.value.startswith("e") or not locator.value[1:].isdigit():
                raise BrowserElementNotFound("Browser observation element reference was invalid.")
            return root.locator(f'[data-audoryn-control-ref="{locator.value}"]')
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
                  const control = el.closest('button,input[type="submit"],input[type="image"]');
                  if (!control) return false;
                  const tag = control.tagName.toLowerCase();
                  const type = (control.getAttribute('type') || '').toLowerCase();
                  if (tag === 'input') {
                    return Boolean(control.form) && (type === 'submit' || type === 'image');
                  }
                  if (tag === 'button') {
                    return Boolean(control.form) && (!type || type === 'submit');
                  }
                  return false;
                }"""
            )
        )

    @staticmethod
    async def _inside_form(target: Locator) -> bool:
        return bool(await target.evaluate("(el) => Boolean(el.closest('form'))"))

    @staticmethod
    async def _control_metadata(target: Locator) -> dict[str, str | None]:
        raw = await target.evaluate(
            """(el) => ({
              type: el.getAttribute('type'),
              name: el.getAttribute('name'),
              id: el.getAttribute('id'),
              label: el.getAttribute('aria-label')
                || (el.labels && el.labels.length ? el.labels[0].innerText : null),
              placeholder: el.getAttribute('placeholder'),
              autocomplete: el.getAttribute('autocomplete')
            })"""
        )
        if not isinstance(raw, dict):
            return {}
        return {str(key): (str(value) if value is not None else None) for key, value in raw.items()}

    async def _assert_not_sensitive_target(self, target: Locator) -> None:
        metadata = await self._control_metadata(target)
        if is_sensitive_field_metadata(
            element_type=metadata.get("type"),
            name=metadata.get("name"),
            element_id=metadata.get("id"),
            label=metadata.get("label"),
            placeholder=metadata.get("placeholder"),
            autocomplete=metadata.get("autocomplete"),
        ):
            raise PermissionError(
                "Sensitive browser fields require Secret Vault credential injection."
            )

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
        # A popup's initial request may be denied before Playwright has emitted
        # the context "page" event. Wait briefly for that page to register, then
        # close only the newly opened page and restore the prior governed page.
        if handle.blocked_popup_initial_navigation:
            original = next(iter(handle.pages.values()), None)
            for _ in range(10):
                current = handle.page
                if len(handle.pages) > 1 and current is not original:
                    await current.close()
                    await asyncio.sleep(0)
                    handle.blocked_popup_initial_navigation = False
                    return
                await asyncio.sleep(0.02)
            handle.blocked_popup_initial_navigation = False

        # Ordinary blocked main-frame navigation stays in the same governed page.
        # Let Chromium's internal error navigation settle before returning.
        await handle.page.wait_for_timeout(200)

    @staticmethod
    async def _settle_page(handle: _SessionHandle, began: float) -> None:
        """Wait, briefly, for what an action started to finish before the page is observed (F48):
        the requests it opened, a page it moved to, and content the page draws after loading. The
        page has settled once nothing changed and none of those requests was open for a short
        while; a page still busy at the limit is observed as it stands. An empty page keeps being
        waited for, as a page drawn by script is empty until its script runs."""
        deadline = time.monotonic() + ACTION_SETTLE_MAX_SECONDS
        previous: object = None
        quiet_since = time.monotonic()
        while time.monotonic() < deadline:
            page = handle.page  # a popup the action opened becomes the active page
            if page.is_closed():
                return
            try:
                signature = await page.evaluate(_PAGE_SIGNATURE_SCRIPT)
            except PlaywrightError:  # the page is moving to another one
                signature = None
            busy = any(started >= began for started in handle.open_requests.get(page, {}).values())
            if busy or signature is None or signature != previous or not signature[0]:
                previous, quiet_since = signature, time.monotonic()
            elif time.monotonic() - quiet_since >= ACTION_QUIET_SECONDS:
                return
            await asyncio.sleep(_SETTLE_POLL_SECONDS)
        telemetry_logger.info(
            "Browser page still busy after an action session=%s; observing it as it stands",
            handle.session.id,
        )

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
        follow_link_timed_out = False
        began = time.monotonic()
        try:
            if operation == "navigation.open":
                if not url:
                    raise ValueError("URL is required for browser navigation.")
                self._preflight(handle, url)
                navigation_started = time.monotonic()
                telemetry_logger.info(
                    "Browser navigation started run=%s session=%s url=%s",
                    handle.session.run_id,
                    handle.session.id,
                    redact_url(url),
                )
                await handle.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                telemetry_logger.info(
                    "Browser navigation DOM ready run=%s session=%s elapsed=%.3fs url=%s",
                    handle.session.run_id,
                    handle.session.id,
                    time.monotonic() - navigation_started,
                    redact_url(handle.page.url),
                )
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
        except PlaywrightTimeoutError as error:
            if handle.blocked_navigation is not None:
                await self._raise_blocked(handle, error)
            if operation != "navigation.follow_link":
                raise
            # The click may have changed a same-page view before Playwright timed out.
            # Observe it so the planner can choose a different grounded action.
            follow_link_timed_out = True
        except PlaywrightError as error:
            await self._raise_blocked(handle, error)
        await self._settle_page(handle, began)
        await self._raise_if_blocked(handle)
        self._raise_if_limit(handle)
        observation_started = time.monotonic()
        observation = await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        if follow_link_timed_out:
            observation = replace(
                observation,
                page_state={
                    **observation.page_state,
                    "actionAttempt": {
                        "status": "timed_out",
                        "operation": "navigation.follow_link",
                    },
                },
            )
        telemetry_logger.info(
            "Browser navigation observation completed run=%s session=%s elapsed=%.3fs url=%s",
            handle.session.run_id,
            handle.session.id,
            time.monotonic() - observation_started,
            redact_url(observation.url),
        )
        self._log_memory("after_navigation")
        return observation

    @staticmethod
    def _interaction_content_changed(
        before: BrowserObservation,
        after: BrowserObservation,
    ) -> bool:
        return (
            before.url != after.url
            or before.title != after.title
            or before.visible_text != after.visible_text
            or before.elements != after.elements
            or before.form_details != after.form_details
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
        axis: str = "vertical",
        focus_text: str | None = None,
        dialog_action: str | None = None,
        prompt_text: str | None = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation:
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        before_interaction = handle.last_observation
        key_timed_out = False
        began = time.monotonic()
        target = self._locator(handle, locator) if locator is not None else None
        self._set_dialog_policy(
            handle,
            action=dialog_action,
            prompt_text=prompt_text,
        )

        if operation == "page.scroll":
            await self._scroll(handle, target, value, axis, focus_text)
        elif target is not None and operation in _ELEMENT_OPERATIONS:
            key_timed_out = await self._act_on_element(handle, target, operation, value, timeout_ms)
        else:
            raise ValueError("Unsupported browser interaction operation.")

        await self._settle_page(handle, began)
        handle.dialog_action = None
        handle.dialog_prompt_text = None
        await self._raise_if_blocked(handle)
        observation = await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        if operation == "element.press_key" and before_interaction is not None:
            observation = await self._await_key_effect(
                session_id,
                organization_id,
                worker_id,
                handle,
                before_interaction,
                observation,
                timeout_ms,
            )
        if key_timed_out:
            observation = replace(
                observation,
                page_state={
                    **observation.page_state,
                    "actionAttempt": {"status": "timed_out", "operation": operation},
                },
            )
            handle.last_observation = observation
        return observation

    async def _act_on_element(
        self,
        handle: _SessionHandle,
        target: Locator,
        operation: str,
        value: object,
        timeout_ms: int,
    ) -> bool:
        """Perform one element action; whether a key press timed out (it may still have
        reached the page, so it is observed and never replayed)."""
        if operation == "element.click":
            if await self._is_submit_control(target):
                raise ValueError(
                    "Submit controls must use the governed browser.form.submit capability."
                )
            await target.click(timeout=timeout_ms)
        elif operation == "element.type":
            await self._assert_not_sensitive_target(target)
            await target.fill(str(value or ""), timeout=timeout_ms)
        elif operation == "element.clear":
            await target.fill("", timeout=timeout_ms)
        elif operation == "element.select":
            await self._assert_not_sensitive_target(target)
            await target.select_option(str(value or ""), timeout=timeout_ms)
        elif operation == "element.check":
            await target.check(timeout=timeout_ms)
        elif operation == "element.uncheck":
            await target.uncheck(timeout=timeout_ms)
        elif operation == "element.hover":
            await target.hover(timeout=timeout_ms)
        else:  # element.press_key
            metadata = await target.evaluate(KEYBOARD_METADATA_SCRIPT)
            if not read_only_key(metadata, str(value or "")):
                raise ValueError(
                    "This key could submit or activate an external write. "
                    "Use an authorized click or browser.form.submit instead."
                )
            # The keyboard capability is read-only. A misleading search label
            # must not turn it into a POST or another external write.
            handle.keyboard_read_only_until = time.monotonic() + timeout_ms / 1000 + 5
            try:
                await target.press(str(value or ""), timeout=timeout_ms)
            except PlaywrightTimeoutError:
                # The key may already have reached the page. Observe and replan;
                # replaying it could activate a second action.
                return True
        return False

    @staticmethod
    async def _scroll(
        handle: _SessionHandle,
        target: Locator | None,
        value: object,
        axis: str,
        focus_text: str | None,
    ) -> None:
        if axis not in {"vertical", "horizontal"}:
            raise ValueError("Browser scroll axis must be vertical or horizontal.")
        if isinstance(value, str) and value.strip().lower() in {"top", "bottom"}:
            if target is not None or focus_text or axis != "vertical":
                raise ValueError("Top and bottom apply only to vertical page scrolling.")
            position = value.strip().lower()
            await handle.page.evaluate(
                "position => window.scrollTo(0, position === 'bottom' "
                "? document.documentElement.scrollHeight : 0)",
                position,
            )
            return
        try:
            amount = int(value) if isinstance(value, (int, float, str)) else 0
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Browser page.scroll value must be an integer, top, or bottom."
            ) from error
        if focus_text and target is not None:
            raise ValueError("Choose either a locator or focusText for browser scrolling.")
        if focus_text:
            did_scroll = await handle.page.evaluate(
                _SECTION_SCROLL_SCRIPT,
                {"query": focus_text[:160], "axis": axis, "amount": amount},
            )
            if not did_scroll:
                raise ValueError("No scrollable container was found in the named section.")
        elif target is not None:
            await target.evaluate(
                "(el, delta) => el.scrollBy({left: delta.x, top: delta.y})",
                {
                    "x": amount if axis == "horizontal" else 0,
                    "y": amount if axis == "vertical" else 0,
                },
            )
        else:
            await handle.page.mouse.wheel(
                amount if axis == "horizontal" else 0,
                amount if axis == "vertical" else 0,
            )

    async def _await_key_effect(
        self,
        session_id: UUID,
        organization_id: UUID,
        worker_id: UUID | None,
        handle: _SessionHandle,
        before: BrowserObservation,
        observation: BrowserObservation,
        timeout_ms: int,
    ) -> BrowserObservation:
        """Keyboard handlers often update the page after the key event returns. Give those
        changes a bounded chance to appear before the planner sees a no-progress observation;
        never replay the key to wait for them."""
        deadline = time.monotonic() + min(5.0, timeout_ms / 1000)
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            await self._raise_if_blocked(handle)
            next_observation = await self.observe(
                session_id,
                organization_id=organization_id,
                worker_id=worker_id,
            )
            settled = self._interaction_content_changed(
                before, next_observation
            ) and not self._interaction_content_changed(observation, next_observation)
            observation = next_observation
            if settled:
                break
        return observation

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
        began = time.monotonic()
        for locator, value in fields:
            target = self._locator(handle, locator)
            if await self._is_submit_control(target):
                raise ValueError("Form fill cannot target a submit control.")
            await self._assert_not_sensitive_target(target)
            await self._set_control_value(target, value, timeout_ms=timeout_ms)
        await self._settle_page(handle, began)
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
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        handle.blocked_navigation = None
        self._set_dialog_policy(
            handle,
            action=dialog_action,
            prompt_text=prompt_text,
        )
        if form_ref is None and submit_locator is None:
            raise ValueError("Form submission requires formRef or submitLocator.")

        # Listen before submitting, so a submit that opens the next page is observed once that page
        # has loaded rather than mid-load (seen as a "Loading <url>" title in the simulator).
        watch = _SubmitWatch.begin(handle.page, timeout_ms)
        began = time.monotonic()
        try:
            await self._submit(handle, form_ref, submit_locator, timeout_ms)
            await self._raise_if_blocked(handle)
            await watch.settle(handle.page, timeout_ms)
        finally:
            watch.close()
        # A script may send the form in the background and open the next page only afterwards
        # (F48), so the page's own work is waited for too.
        await self._settle_page(handle, began)
        await self._raise_if_blocked(handle)
        return await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )

    async def _submit(
        self,
        handle: _SessionHandle,
        form_ref: str | None,
        submit_locator: BrowserLocator | None,
        timeout_ms: int,
    ) -> None:
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
            await asyncio.sleep(0.05)
        except PlaywrightError as error:
            await self._raise_blocked(handle, error)
        finally:
            handle.dialog_action = None
            handle.dialog_prompt_text = None

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

        # A form's method is not checked: sites that send their login with JavaScript leave it
        # out. Every request is checked instead, and one carrying a secret in its web address is
        # refused (F45).
        credentials.require(tuple(key for _, key in bindings))
        handle.sensitive_values.update(credentials.sensitive_values())
        for locator, credential_key in bindings:
            target = self._locator(handle, locator)
            value = credentials.resolve(credential_key)
            handle.sensitive_values.add(value)
            if credentials.is_secret(credential_key):
                handle.login_secrets.add(value)
            await self._set_control_value(target, value, timeout_ms=timeout_ms)

        try:
            observation = await self.submit_form(
                session_id,
                organization_id=organization_id,
                worker_id=worker_id,
                form_ref=form_ref,
                submit_locator=submit_locator,
                timeout_ms=timeout_ms,
            )
        except BrowserNavigationBlocked as blocked:
            if blocked.decision.reason == LOGIN_IN_ADDRESS:
                raise BrowserAuthenticationFailure(LOGIN_IN_ADDRESS) from blocked
            raise
        if failure_text and failure_text in observation.visible_text:
            raise BrowserAuthenticationFailure("Browser authentication was rejected.")
        if success_url_contains and success_url_contains not in observation.url:
            raise BrowserAuthenticationFailure(
                "Browser authentication did not reach the expected destination."
            )
        return observation

    async def screenshot(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
    ) -> bytes:
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        # Mask all editable text fields in evidence images. This is intentionally
        # broader than password-only masking so screenshot evidence never becomes
        # a side channel for credentials or sensitive form content.
        masks: list[Locator] = []
        for frame in handle.page.frames:
            masks.extend(
                [
                    frame.locator("input"),
                    frame.locator("textarea"),
                    frame.locator("[contenteditable='true']"),
                ]
            )
        full_page_evidence = not (
            handle.navigation_policy is not None
            and not handle.navigation_policy.load_visual_resources
        )
        screenshot_started = time.monotonic()
        telemetry_logger.info(
            "Browser screenshot started run=%s session=%s full_page=%s url=%s",
            handle.session.run_id,
            handle.session.id,
            full_page_evidence,
            redact_url(handle.page.url),
        )
        content = await handle.page.screenshot(
            full_page=full_page_evidence,
            animations="disabled",
            mask=masks,
        )
        telemetry_logger.info(
            "Browser screenshot completed run=%s session=%s full_page=%s bytes=%s elapsed=%.3fs",
            handle.session.run_id,
            handle.session.id,
            full_page_evidence,
            len(content),
            time.monotonic() - screenshot_started,
        )
        self._log_memory("after_screenshot", screenshot_bytes=len(content))
        return content

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
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        if not content:
            raise ValueError("Browser upload artifact is empty.")
        self._set_dialog_policy(
            handle,
            action=dialog_action,
            prompt_text=prompt_text,
        )
        target = self._locator(handle, locator)
        input_type = await target.get_attribute("type")
        if (input_type or "").lower() != "file":
            raise ValueError("Browser upload target must be a file input.")
        began = time.monotonic()
        try:
            await target.set_input_files(
                {
                    "name": name,
                    "mimeType": media_type,
                    "buffer": content,
                },
                timeout=timeout_ms,
            )
            await asyncio.sleep(0.025)
            uploaded_name = await target.evaluate(
                "(el) => el.files && el.files.length ? el.files[0].name : null"
            )
        finally:
            handle.dialog_action = None
            handle.dialog_prompt_text = None
        if uploaded_name != name:
            raise RuntimeError("Browser upload input did not retain the authorized artifact.")
        await self._settle_page(handle, began)
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
        max_bytes: int = 20 * 1024 * 1024,
    ) -> tuple[BrowserDownload, BrowserObservation]:
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        target = self._locator(handle, locator)
        async with handle.page.expect_download(timeout=timeout_ms) as download_info:
            await target.click(timeout=timeout_ms)
        download = await download_info.value
        failure = await download.failure()
        if failure:
            raise BrowserDownloadFailure("Browser download failed before artifact persistence.")

        source_url = download.url
        policy = handle.navigation_policy
        if policy is None:
            await download.delete()
            raise PermissionError("Browser download requires destination policy.")
        decision = policy.permits(source_url, source_url=handle.page.url)
        if not decision.allowed:
            await download.delete()
            raise BrowserNavigationBlocked(decision)

        download_path = await download.path()
        if download_path is None:
            raise BrowserDownloadFailure(
                "Browser download did not expose managed temporary content."
            )
        content = await asyncio.to_thread(Path(download_path).read_bytes)
        if len(content) > max_bytes:
            await download.delete()
            raise BrowserRuntimeLimitExceeded("Browser download exceeds the configured size limit.")
        name = download.suggested_filename or "download.bin"
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        await download.delete()
        observation = await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        return (
            BrowserDownload(
                name=name,
                source_url=source_url,
                content=content,
                media_type=media_type,
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
        handle = self._handle(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        observation = await self.observe(
            session_id,
            organization_id=organization_id,
            worker_id=worker_id,
        )
        checks: dict[str, bool] = {}

        if expectation.url_changed_from is not None:
            checks["urlChanged"] = observation.url != expectation.url_changed_from
        if expectation.url_equals is not None:
            checks["urlEquals"] = observation.url == expectation.url_equals
        if expectation.url_contains is not None:
            checks["urlContains"] = expectation.url_contains in observation.url
        if expectation.expected_text is not None:
            checks["expectedText"] = expectation.expected_text in observation.visible_text
        if expectation.absent_text is not None:
            checks["absentText"] = expectation.absent_text not in observation.visible_text
        if expectation.success_indicator is not None:
            checks["successIndicator"] = expectation.success_indicator in observation.visible_text
        if expectation.error_indicator is not None:
            checks["errorIndicatorAbsent"] = (
                expectation.error_indicator not in observation.visible_text
            )

        if expectation.element_appeared is not None:
            target = self._locator(handle, expectation.element_appeared)
            count = await target.count()
            checks["elementAppeared"] = count > 0 and await target.first.is_visible()
        if expectation.element_disappeared is not None:
            target = self._locator(handle, expectation.element_disappeared)
            count = await target.count()
            checks["elementDisappeared"] = count == 0 or not await target.first.is_visible()
        if expectation.form_locator is not None:
            target = self._locator(handle, expectation.form_locator)
            current_value = await target.input_value()
            checks["formState"] = (
                expectation.form_value is None or current_value == expectation.form_value
            )
        if expectation.page_state_key is not None:
            checks["pageState"] = (
                observation.page_state.get(expectation.page_state_key)
                == expectation.page_state_value
            )
        if expectation.download_created is not None:
            created = isinstance(result_output.get("downloadArtifact"), dict)
            checks["downloadCreated"] = created is expectation.download_created

        verified = bool(checks) and all(checks.values())
        details_raw = {
            "checks": checks,
            "url": observation.url,
            "observationId": observation.id,
        }
        details = redact_sensitive_structure(details_raw)
        assert isinstance(details, dict)
        return BrowserVerificationProbe(
            verified=verified,
            details=details,
        )
