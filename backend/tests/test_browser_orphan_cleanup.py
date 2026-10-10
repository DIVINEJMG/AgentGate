from __future__ import annotations

import signal

import pytest

import app.execution.browser.runtime as browser_runtime_module
from app.execution.browser.runtime import BrowserRuntime


@pytest.mark.asyncio
async def test_orphan_cleanup_uses_available_force_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter(({12345}, {12345}, set()))
    sent: list[tuple[int, signal.Signals]] = []

    monkeypatch.setattr(
        browser_runtime_module, "chromium_process_ids", lambda: next(snapshots)
    )
    monkeypatch.setattr(
        browser_runtime_module.os,
        "kill",
        lambda pid, selected_signal: sent.append((pid, selected_signal)),
    )

    await BrowserRuntime()._reap_orphaned_chromium()

    assert sent == [
        (12345, signal.SIGTERM),
        (12345, getattr(signal, "SIGKILL", signal.SIGTERM)),
    ]
