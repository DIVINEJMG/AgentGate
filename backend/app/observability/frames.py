"""Where an error happened in AgentGate's own code, for logs (never the error's message, which may
hold conversation or task content)."""
from __future__ import annotations

import traceback


def app_frames(exc: BaseException, limit: int = 4) -> str:
    """The innermost frames inside `app/` (library frames skipped), innermost first."""
    paths = [(frame, frame.filename.replace("\\", "/")) for frame in traceback.extract_tb(exc.__traceback__)]
    inside = [(frame, path.rsplit("/app/", 1)[-1]) for frame, path in paths if "/app/" in path][-limit:]
    return " < ".join(f"{path}:{frame.lineno} {frame.name}" for frame, path in reversed(inside))
