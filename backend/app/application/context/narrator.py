"""The plain sentence that says what a step did: shown to the person as progress and kept in the
planner's action history.

The verb comes from the operation, never from the output's shape, so a write never reads as a
read: "Created issue #7 …", "Edited README.md …", "Found 3 issues: …".
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from app.application.context.evidence import as_list, as_map, list_of, map_of

WRITE_VERBS = {
    "create": "Created", "update": "Updated", "set": "Set", "add": "Added", "archive": "Archived",
    "merge": "Merged", "submit": "Submitted", "resolve": "Resolved", "request": "Requested",
    "dispatch": "Started", "rerun": "Re-ran", "cancel": "Cancelled", "publish": "Published",
    "upload": "Uploaded", "edit": "Edited", "patch": "Patched", "rollback": "Rolled back",
}
NAME_KEYS = ("title", "name", "fullName", "full_name", "path")
NUMBER_KEYS = ("number", "issue_number", "pull_number")
# Nouns a sentence leaves out: a workspace step names the file it changed.
UNNAMED_NOUNS = frozenset({"workspace"})
SHOWN = 3

Narrator = Callable[[dict[str, object], Mapping[str, object]], str]


def _short(sha: object) -> str:
    return str(sha or "")[:7]


def _title(record: Mapping[str, object], limit: int) -> str | None:
    return next((str(record[key])[:limit] for key in NAME_KEYS if record.get(key)), None)


def _name(record: Mapping[str, object], limit: int = 80) -> str | None:
    """'#7 Title', 'README.md', '#7' or None."""
    title, number = _title(record, limit), record.get("number")
    if title is None:
        return None if number is None else f"#{number}"
    return (f"#{number} " if number is not None else "") + title


def _names(rows: list[object]) -> str:
    names = [name for row in rows[:SHOWN] if (name := _name(as_map(row), 60) or _ref(as_map(row)))]
    return "; ".join(names) + ("; …" if len(rows) > SHOWN and names else "")


def _ref(record: Mapping[str, object]) -> str | None:
    value = record.get("id")
    return None if value is None else str(value)


def singular(noun: str) -> str:
    for plural, single in (("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("sses", "ss"), ("s", "")):
        if noun.endswith(plural):
            return noun[: -len(plural)] + single
    return noun


def noun_of(scope: str) -> str:
    """What an operation acts on, in words: "issue", "pull request", or "" for the workspace."""
    parts = scope.split(".")
    noun = parts[-2] if len(parts) >= 3 else ""
    return "" if noun in UNNAMED_NOUNS else singular(noun.replace("_", " "))


def _count(count: int, noun: str) -> str:
    return f"1 {singular(noun)}" if count == 1 else f"{count} {noun}"


def _noun(scope: str) -> str:
    noun = scope.split(".")[-2].replace("_", " ") if scope.count(".") >= 2 else ""
    return noun if noun.endswith("s") else "results"


def _found(scope: str, output: object) -> str | None:
    """A read: how many items and the first few names, or the one object it read."""
    items, noun, state = list_of(output), _noun(scope), None
    if (record := map_of(output)) is not None:
        lists = [(key, found) for key, value in record.items() if (found := list_of(value)) is not None]
        if len(lists) != 1:
            return f"Read {_name(record)}." if _title(record, 80) else None
        (key, items), state = lists[0], record.get("state")
        noun = key.replace("_", " ")
    if items is None:
        return None
    if not items:
        # Say what was empty, and keep a state the record carries (F32).
        return (f"State: {state}; no {noun}." if isinstance(state, str) and state else f"No {noun}.")
    names = _names(items)
    return f"Found {_count(len(items), _noun(scope))}" + (f": {names}" if names else "") + "."


def _written(scope: str, output: object, request: Mapping[str, object]) -> str:
    """A write: its verb, what it wrote, and the change in brief when one is reported."""
    action = scope.rsplit(".", 1)[-1]
    record = as_map(output)
    subject = _name(record) or _title(request, 80)  # a number in the input is the target, below
    target = next((request[key] for key in NUMBER_KEYS if request.get(key) is not None), None)
    sentence = WRITE_VERBS[action] + (f" {noun_of(scope)}" if noun_of(scope) else "")
    if (items := list_of(output)) is not None:
        listed = _names(items) or "none"
        return sentence + (f" on #{target}: {listed}" if target else "") + "."
    if subject:
        sentence += f" {subject}"
    if target is not None and not (subject or "").startswith("#"):
        sentence += f" on #{target}"
    detail = record.get("diffStat")
    return sentence + (f" ({detail})" if detail else "") + "."


def _branch_name(record: Mapping[str, object]) -> str:
    return str(record.get("ref") or "").removeprefix("refs/heads/")


def _sha(record: Mapping[str, object]) -> str:
    return _short(as_map(record.get("object")).get("sha"))


def _changed_files(record: dict[str, object], verb: str) -> str:
    changes = as_list(record.get("changes"))
    return f"{verb} with {_count(len(changes), 'changed files')}" + (f": {_names(changes)}" if changes else "") + "."


def _pull_request(record: Mapping[str, object], request: Mapping[str, object], done: str) -> str:
    subject = _name(record) or _name(request)
    return f"{done} pull request" + (f" {subject}" if subject else "")


SPECIAL: dict[str, Narrator] = {
    "pull_request.create": lambda record, request: _pull_request(
        record, request, "Opened draft" if record.get("draft") or request.get("draft") else "Opened") + ".",
    "pull_request.ready": lambda record, request: _pull_request(record, request, "Marked") + " ready for review.",
    "branch.create": lambda record, _: f"Created branch {_branch_name(record)} at {_sha(record)}.",
    "commit.create": lambda record, _: f"Committed {_sha(record)} to {_branch_name(record)}.",
    "workspace.open": lambda record, _: f"Opened a workspace at {_short(record.get('baseSha'))}.",
    "workspace.diff": lambda record, _: _changed_files(record, "Compared the workspace"),
    "workspace.close": lambda record, _: _changed_files(record, "Closed the workspace"),
}


# What a browser step did, before saying which page it is on (F44).
BROWSER_VERBS = {
    "navigation.open": "Opened", "navigation.follow_link": "Followed a link to",
    "navigation.back": "Went back to", "navigation.forward": "Went forward to",
    "navigation.reload": "Reloaded", "page.read": "Read", "auth.login": "Signed in; now on",
    "form.submit": "Submitted the form; now on",
}


def _browser(scope: str, record: Mapping[str, object]) -> str | None:
    """A browser step: the file it downloaded or chose (with the file's id, so a later step can use
    it), or the page it is on now. A page observation is never counted as results."""
    if download := as_map(record.get("downloadArtifact")):
        return (f"Downloaded {download.get('name') or 'a file'} ({download.get('sizeBytes')} bytes; "
                f"file id {download.get('id')}).")
    if upload := as_map(record.get("uploadArtifact")):
        return f"Chose {upload.get('name') or 'the file'} ({upload.get('sizeBytes')} bytes) in the upload field."
    if as_map(record.get("actionEvidence")).get("result") == "not_sent":
        # A click whose change was held for a governed submit (F50).
        return "Held the change this control sends to the site; nothing was sent."
    page = as_map(record.get("observation"))
    title, url = str(page.get("title") or "").strip()[:120], str(page.get("url") or "")
    where = f"{title} ({url})" if title and url else title or url
    if not where:
        return None
    return f"{BROWSER_VERBS.get(scope.split('.', 1)[-1], 'Now on')} {where}."


def narrate(scope: str, output: object, action_input: Mapping[str, object] | None = None) -> str | None:
    """One sentence for a completed step, or None when there is nothing plain to say (the
    caller then uses the provider's verification note)."""
    request = action_input or {}
    if scope.startswith("browser."):
        return _browser(scope, as_map(output))
    special = SPECIAL.get(".".join(scope.split(".")[-2:]))
    if special is not None:
        return special(as_map(output), request)
    if scope.rsplit(".", 1)[-1] in WRITE_VERBS and scope.count(".") >= 2:
        return _written(scope, output, request)
    return _found(scope, output)
