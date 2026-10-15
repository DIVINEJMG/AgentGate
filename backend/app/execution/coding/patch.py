"""Workspace changes as a git-format patch: what a model reads as the diff, and what `git apply`
accepts."""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping

NO_FINAL_NEWLINE = "\\ No newline at end of file\n"


def _lines(content: object, binary_note: str) -> list[str]:
    return (content if isinstance(content, str) else binary_note).splitlines(keepends=True)


def file_patch(path: str, before: object, after: object) -> str:
    """One file's change; None means the file is absent on that side."""
    header = f"diff --git a/{path} b/{path}\n"
    if before is None:
        header += "new file mode 100644\n"
    elif after is None:
        header += "deleted file mode 100644\n"
    hunks = difflib.unified_diff(
        [] if before is None else _lines(before, "[Binary content]\n"),
        [] if after is None else _lines(after, "[Binary content changed]\n"),
        "/dev/null" if before is None else f"a/{path}",
        "/dev/null" if after is None else f"b/{path}",
    )
    # A last line without a newline is marked the way git marks it, so the next line starts clean.
    return header + "".join(
        line if line.endswith("\n") else line + "\n" + NO_FINAL_NEWLINE for line in hunks
    )


def workspace_patch(
    paths: Iterable[str], original: Mapping[str, object], current: Mapping[str, object]
) -> str:
    return "".join(file_patch(path, original.get(path), current.get(path)) for path in paths)
