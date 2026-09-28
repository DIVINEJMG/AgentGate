from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BrowserMemorySnapshot:
    python_rss_bytes: int
    chromium_rss_bytes: int
    renderer_rss_bytes: int
    cgroup_current_bytes: int | None
    cgroup_limit_bytes: int | None
    active_sessions: int
    open_pages: int

    @property
    def cgroup_percent(self) -> float | None:
        if self.cgroup_current_bytes is None or self.cgroup_limit_bytes in (None, 0):
            return None
        return (self.cgroup_current_bytes / self.cgroup_limit_bytes) * 100.0

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "pythonRssBytes": self.python_rss_bytes,
            "chromiumRssBytes": self.chromium_rss_bytes,
            "rendererRssBytes": self.renderer_rss_bytes,
            "cgroupCurrentBytes": self.cgroup_current_bytes,
            "cgroupLimitBytes": self.cgroup_limit_bytes,
            "cgroupPercent": self.cgroup_percent,
            "activeSessions": self.active_sessions,
            "openPages": self.open_pages,
        }


def _read_int(path: str) -> int | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _rss_bytes(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                return int(parts[1]) * 1024 if len(parts) >= 2 else 0
    except (OSError, ValueError):
        return 0
    return 0


def _chromium_memory() -> tuple[int, int]:
    chromium = 0
    renderers = 0
    proc = Path("/proc")
    if not proc.exists():
        return (0, 0)
    for child in proc.iterdir():
        if not child.name.isdigit():
            continue
        try:
            cmdline = (
                (child / "cmdline")
                .read_bytes()
                .replace(b"\x00", b" ")
                .decode("utf-8", errors="ignore")
                .lower()
            )
        except OSError:
            continue
        if not any(marker in cmdline for marker in ("chromium", "chrome-headless", "/chrome ")):
            continue
        rss = _rss_bytes(int(child.name))
        chromium += rss
        if "--type=renderer" in cmdline:
            renderers += rss
    return chromium, renderers


def _cgroup_memory() -> tuple[int | None, int | None]:
    current = _read_int("/sys/fs/cgroup/memory.current")
    limit = _read_int("/sys/fs/cgroup/memory.max")
    if current is not None:
        return current, limit
    return (
        _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
        _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    )


def browser_memory_snapshot(*, active_sessions: int, open_pages: int) -> BrowserMemorySnapshot:
    chromium, renderers = _chromium_memory()
    current, limit = _cgroup_memory()
    return BrowserMemorySnapshot(
        python_rss_bytes=_rss_bytes(os.getpid()),
        chromium_rss_bytes=chromium,
        renderer_rss_bytes=renderers,
        cgroup_current_bytes=current,
        cgroup_limit_bytes=limit,
        active_sessions=active_sessions,
        open_pages=open_pages,
    )
