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
        "allowedPaths": {"type": "array", "items": {"type": "string"}},
        "deniedPaths": {"type": "array", "items": {"type": "string"}},
        "branches": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Existing branches the human named for this task to change, exactly as written. A task "
                "otherwise writes only to its own new branches."
            ),
        },
        "pullRequests": {
            "type": "array",
            "items": {"type": "integer"},
            "description": (
                "Numbers of existing pull requests the human named for this task to change (for example "
                "#8); their head branches may then be written."
            ),
        },
    },
    "additionalProperties": False,
}
# "#8", "PR 8", "pull request #8": a pull request the person named.
NAMED_PULL_REQUEST = re.compile(r"(?:#|\bpr\s*#?|\bpull\s+request\s*#?)(\d+)", re.IGNORECASE)


def grounded_constraints(constraints: dict, instruction: str) -> dict:
    """Branch and pull-request grants the person named in their own words; any other is dropped,
    so model or repository text can never widen what a task may write."""
    named = {int(number) for number in NAMED_PULL_REQUEST.findall(instruction)}
    grounded = dict(constraints)
    if "branches" in grounded:
        grounded["branches"] = [
            branch
            for branch in grounded["branches"]
            if branch and re.search(rf"(?<![\w/.-]){re.escape(str(branch))}(?![\w/-])", instruction)
        ]
    if "pullRequests" in grounded:
        grounded["pullRequests"] = [
            number for number in grounded["pullRequests"] if number in named
        ]
    return grounded


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


def task_branch_prefix(work_item_id: object) -> str:
    """Every branch a task creates starts with this, unless its constraints grant another."""
    return f"codex/{str(work_item_id)[:8]}/"


def validate_task_branch(
    branch: str, default_branch: str, work_item_id, constraints=None, pull_request=None
):
    """A task writes only to its own branches, to branches granted by name, or to the head branch
    of a granted pull request (`pull_request` is the PR being changed); never to default or
    protected branches."""
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
    prefix = task_branch_prefix(work_item_id)
    granted_pr = pull_request is not None and pull_request in constraints.get("pullRequests", [])
    if branch not in exact and not branch.startswith(prefix) and not granted_pr:
        raise ValueError(
            f"Use this task's branch prefix {prefix}, or a branch or pull request the person named "
            "(granted in task preparation)."
        )
