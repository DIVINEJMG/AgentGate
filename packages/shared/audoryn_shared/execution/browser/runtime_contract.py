"""What the browser provider needs from a browser runtime (shared). The Playwright runtime that
implements it (runtime.py) runs only in the execution service."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from audoryn_shared.execution.browser.contracts import (
    BrowserDownload,
    BrowserLocator,
    BrowserObservation,
    BrowserSession,
    BrowserVerificationProbe,
)
from audoryn_shared.execution.browser.credentials import BrowserCredentialBundle
from audoryn_shared.execution.browser.policy import BrowserDomainPolicy
from audoryn_shared.execution.browser.verification import BrowserVerificationExpectation


class BrowserRuntimeContract(Protocol):
    async def create_session(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        ttl_seconds: int = 1800,
        navigation_policy: BrowserDomainPolicy | None = None,
        organization_sessions: int | None = None,
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
