"""Runs inside the coding sandbox (standard library only): idempotent edits, a local git checkpoint
after every change, and rollback to a checkpoint. Checkpoints are never pushed; publication stays a
separate, approved GitHub action. Each command prints one JSON object; errors exit with code 2.

    python3 helper.py ROOT init
    python3 helper.py ROOT edit REQUEST.json      {"path", "mode": write|append|replace|delete, ...}
    python3 helper.py ROOT patch PATCH_FILE
    python3 helper.py ROOT rollback COMMIT
    python3 helper.py ROOT commit LABEL           (after files were written from outside, e.g. a restore)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

GENERATED = (
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "__pycache__",
    "*.pyc",
    ".coverage",
    ".coverage.*",
)
PREVIEW_CHARS = 1500


class StepError(Exception):
    pass


def _git(root: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.email=workspace@audoryn.local",
            "-c",
            "user.name=Audoryn",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise StepError(
            "git " + args[0] + " failed: " + (result.stderr or result.stdout).strip()[:300]
        )
    return result.stdout


def init(root: str) -> dict:
    """A local repository whose first commit is the pinned baseline. Safe to call again."""
    if not os.path.isdir(os.path.join(root, ".git")):
        _git(root, "init", "-q")
        with open(os.path.join(root, ".git", "info", "exclude"), "a", encoding="utf-8") as handle:
            handle.write("\n".join(GENERATED) + "\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "--allow-empty", "-m", "baseline")
    baseline = _git(root, "rev-list", "--max-parents=0", "HEAD").split()[0]
    return {"baseline": baseline, "checkpoint": _git(root, "rev-parse", "HEAD").strip()}


def _safe(root: str, path: str) -> str:
    full = os.path.realpath(os.path.join(root, path))
    if (
        os.path.commonpath([full, os.path.realpath(root)]) != os.path.realpath(root)
        or "/.git" in "/" + path
    ):
        raise StepError("Path is outside the workspace.")
    return full


def _read(full: str) -> str | None:
    if not os.path.exists(full):
        return None
    with open(full, encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(full: str, content: str) -> None:
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def _commit(root: str, label: str) -> tuple[str, list[dict]]:
    """Commit everything changed as one checkpoint; returns it and the changed paths."""
    _git(root, "add", "-A")
    if not _git(root, "status", "--porcelain"):
        return _git(root, "rev-parse", "HEAD").strip(), []
    _git(root, "commit", "-q", "-m", label[:200])
    changed = []
    for line in _git(root, "diff", "--name-status", "--no-renames", "HEAD~1", "HEAD").splitlines():
        status, _, path = line.partition("\t")
        changed.append({"path": path, "deleted": status.startswith("D")})
    return _git(root, "rev-parse", "HEAD").strip(), changed


def _summary(root: str, changed: list[dict]) -> dict:
    stat = _git(root, "diff", "--shortstat", "HEAD~1", "HEAD", check=False).strip()
    preview = _git(root, "diff", "--unified=2", "HEAD~1", "HEAD", check=False)
    return {
        "diffStat": stat,
        "preview": preview[:PREVIEW_CHARS],
        "previewTruncated": len(preview) > PREVIEW_CHARS,
        "changed": changed,
    }


def edit(root: str, request: dict) -> dict:
    """One file edit that is safe to repeat: a repeated request finds its work already done."""
    init(root)
    path = str(request.get("path") or "")
    mode = request.get("mode") or "write"
    full = _safe(root, path)
    current = _read(full)
    already = False
    if mode == "write":
        content = str(request.get("content") or "")
        already = current == content
        if not already:
            _write(full, content)
    elif mode == "append":
        content = str(request.get("content") or "")
        offset = request.get("offset")
        existing = current or ""
        if not isinstance(offset, int) or offset < 0:
            raise StepError("Append needs offset: the file's current length (0 for a new file).")
        if len(existing) == offset:
            _write(full, existing + content)
        elif len(existing) == offset + len(content) and existing.endswith(content):
            already = True
        else:
            raise StepError(
                f"{path} is {len(existing)} characters long, not {offset}; read it and append at its "
                "current length."
            )
    elif mode == "replace":
        if current is None:
            raise StepError(f"{path} does not exist; create it with mode write.")
        pairs = request.get("replacements") or []
        if not isinstance(pairs, list) or not pairs:
            raise StepError("Replace needs replacements: a list of {find, replace}.")
        updated, applied = current, 0
        for pair in pairs:
            find, replace = str(pair.get("find") or ""), str(pair.get("replace") or "")
            if not find:
                raise StepError("Each replacement needs non-empty find text.")
            count = updated.count(find)
            # Already applied: the replacement is there and every match of the find text is inside it
            # (the find text may be part of its own replacement, e.g. "a + b" -> "a + b + 0").
            if (
                replace
                and replace in updated
                and count == updated.count(replace) * replace.count(find)
            ):
                continue
            if count == 1:
                updated, applied = updated.replace(find, replace), applied + 1
            elif count > 1:
                raise StepError(
                    f"The text to replace appears {count} times in {path}; include more surrounding "
                    "lines so it matches once."
                )
            elif not replace or replace in updated:
                continue  # already applied (or text to remove is already gone)
            else:
                raise StepError(
                    f"The text to replace was not found in {path}: {find[:80]!r}. Read the file "
                    "and copy the exact current text."
                )
        already = applied == 0
        if not already:
            _write(full, updated)
    elif mode == "delete":
        already = current is None
        if not already:
            os.remove(full)
    else:
        raise StepError("Unknown edit mode; use write, append, replace or delete.")
    checkpoint, changed = _commit(root, f"edit {path}")
    final = _read(full)
    result = {
        "path": path,
        "mode": mode,
        "alreadyApplied": already,
        "checkpoint": checkpoint,
        "size": len(final) if final is not None else 0,
        "exists": final is not None,
        "sha256": hashlib.sha256(final.encode()).hexdigest() if final is not None else None,
    }
    return {
        **result,
        **(_summary(root, changed) if changed else {"diffStat": "", "preview": "", "changed": []}),
    }


def patch(root: str, patch_file: str) -> dict:
    init(root)
    if (
        subprocess.run(
            ["git", "apply", "--check", patch_file], cwd=root, capture_output=True, check=False
        ).returncode
        == 0
    ):
        _git(root, "apply", patch_file)
        checkpoint, changed = _commit(root, "patch")
        return {"alreadyApplied": False, "checkpoint": checkpoint, **_summary(root, changed)}
    if (
        subprocess.run(
            ["git", "apply", "-R", "--check", patch_file],
            cwd=root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    ):
        return {
            "alreadyApplied": True,
            "checkpoint": _git(root, "rev-parse", "HEAD").strip(),
            "changed": [],
            "diffStat": "",
            "preview": "",
        }
    raise StepError(
        "The patch does not apply to the current files; read them and send a patch against their "
        "current content."
    )


def rollback(root: str, commit: str) -> dict:
    """Return the workspace to a checkpoint. Repeating it changes nothing."""
    init(root)
    before = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "cat-file", "-e", commit + "^{commit}")
    _git(root, "reset", "-q", "--hard", commit)
    _git(root, "clean", "-fdq")  # ignored files (dependencies, caches) are kept
    changed = []
    for line in _git(root, "diff", "--name-status", "--no-renames", commit, before).splitlines():
        path = line.partition("\t")[2]
        changed.append({"path": path, "deleted": not os.path.exists(os.path.join(root, path))})
    return {"checkpoint": commit, "undone": before != commit, "changed": changed}


def commit(root: str, label: str) -> dict:
    init(root)
    checkpoint, changed = _commit(root, label)
    return {"checkpoint": checkpoint, "changed": changed}


def main(argv: list[str]) -> int:
    root, command, *args = argv
    try:
        if command == "init":
            result = init(root)
        elif command == "edit":
            with open(args[0], encoding="utf-8") as handle:
                result = edit(root, json.load(handle))
        elif command == "patch":
            result = patch(root, args[0])
        elif command == "rollback":
            result = rollback(root, args[0])
        elif command == "commit":
            result = commit(root, args[0])
        else:
            raise StepError("Unknown command.")
    except StepError as error:
        print(json.dumps({"error": str(error)}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
