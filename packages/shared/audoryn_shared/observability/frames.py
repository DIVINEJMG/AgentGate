"""Where an error happened in AgentGate's own code, for logs (never the error's message, which may
hold conversation or task content)."""
from __future__ import annotations

import re
import traceback


def app_frames(exc: BaseException, limit: int = 4) -> str:
    """The innermost frames inside AgentGate's code (library frames skipped), innermost first, each
    named by its path inside its package (app, audoryn_shared or audoryn_execution)."""
    paths = [(frame, frame.filename.replace("\\", "/")) for frame in traceback.extract_tb(exc.__traceback__)]
    inside = [(frame, _inside(path)) for frame, path in paths if _inside(path)][-limit:]
    return " < ".join(f"{path}:{frame.lineno} {frame.name}" for frame, path in reversed(inside))


# The last package folder in the path (the image itself runs from /app).
_PACKAGES = re.compile(r".*/(?:app|audoryn_shared|audoryn_execution)/(.+)$")


def _inside(path: str) -> str | None:
    match = _PACKAGES.search(path)
    return match.group(1) if match else None
