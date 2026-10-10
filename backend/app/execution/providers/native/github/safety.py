"""Repository content is untrusted; validate publication independently of AI."""

from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

SECRET = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\Z)|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[A-Z0-9]{16})"
)

TASK_CONSTRAINT_SCHEMA = {
    "type": "object",
    "properties": {
        key: {"type": "array", "items": {"type": "string"}}
        for key in ("allowedPaths", "deniedPaths", "branches")
    },
    "additionalProperties": False,
}


def validate_constraints(constraints):
    from jsonschema import validate

    validate(constraints, TASK_CONSTRAINT_SCHEMA)


def safe_path(path: str) -> str:
    parts = PurePosixPath(path).parts
    if (
        not path
        or path.startswith(("/", "\\"))
        or "\\" in path
        or any(p in {"..", ".git"} for p in parts)
        or ":" in path
        or "\x00" in path
    ):
        raise ValueError("Repository paths must be relative and cannot target Git metadata.")
    if any(
        (
            p.casefold().startswith(".env")
            and p.casefold() not in {".env.example", ".env.sample", ".env.template"}
        )
        or p.casefold() in {"id_rsa", "id_ed25519"}
        for p in parts
    ):
        raise ValueError("Credential files cannot be published by the worker.")
    return path


def validate_changes(changes: list[dict], constraints: dict | None = None):
    constraints = constraints or {}
    validate_constraints(constraints)
    seen = set()
    size = 0
    for change in changes:
        path = safe_path(change["path"])
        if path in seen:
            raise ValueError("A commit cannot contain the same path twice.")
        seen.add(path)
        allowed, denied = constraints.get("allowedPaths", []), constraints.get("deniedPaths", [])
        if (allowed and not any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed)) or any(
            fnmatch.fnmatchcase(path, pattern) for pattern in denied
        ):
            raise ValueError("The file is outside the task's path authority.")
        content = change.get("content", "")
        if not isinstance(content, str):
            raise ValueError(  # noqa: TRY004 - validation errors share a safe provider boundary
                "Binary changes require an unavailable publication capability; existing binary assets are retained."
            )
        if not change.get("delete") and "content" not in change:
            raise ValueError("File creation/update requires content.")
        if SECRET.search(content):
            raise ValueError("A possible credential was found in the proposed change.")
        size += len(content.encode())
    if len(changes) > 100 or size > 5_000_000:
        raise ValueError("Commit changes exceed the publication size budget.")


def validate_task_branch(branch: str, default_branch: str, work_item_id, constraints=None):
    constraints = constraints or {}
    validate_constraints(constraints)
    if (
        branch == default_branch
        or branch in {"main", "master", "develop"}
        or branch.startswith("refs/")
        or ".." in branch
        or any(c in branch for c in " ~^:?*[\\")
    ):
        raise ValueError(
            "Direct writes to default/protected branches and unsafe references are unavailable."
        )
    exact = constraints.get("branches", [])
    prefix = f"codex/{str(work_item_id)[:8]}/"
    if branch not in exact and not branch.startswith(prefix):
        raise ValueError(f"Use this task's branch prefix {prefix} or an explicitly granted branch.")
