"""What AgentGate tells the person: one catalogue of plain, specific messages that name the thing
and say what happens next. Internal detail (policy reasons, rule text) stays in the audit record."""
from __future__ import annotations

from collections.abc import Mapping

from audoryn_execution.application.context.narrator import NUMBER_KEYS, noun_of

# What a write does (to ask for it) and what it did (to say whether it happened).
TO_DO = {"ready": "mark {o} ready for review", "dispatch": "start {o}", "rerun": "re-run {o}",
         "rollback": "roll back {o}"}
DONE = {"create": "created", "update": "updated", "set": "set", "add": "added", "archive": "archived",
        "merge": "merged", "submit": "submitted", "resolve": "resolved", "request": "requested",
        "dispatch": "started", "rerun": "re-run", "cancel": "cancelled", "publish": "published",
        "upload": "uploaded", "edit": "edited", "patch": "patched", "rollback": "rolled back",
        "ready": "marked ready for review"}
CHOICES = "It happened, It didn't happen, or Check again"
SAVED = "Completed steps are saved; nothing was rolled back."


def write_object(scope: str, request: Mapping[str, object]) -> str:
    """What a write acts on: 'the issue “Simulation test”', 'pull request #8',
    'branch audoryn/55ea0379/readme-setup', 'a comment on #7'."""
    noun = noun_of(scope) or "change"
    title = request.get("title") or request.get("name")
    named = next((request[key] for key in ("branch", "head", "path", "tag_name") if request.get(key)), None)
    number = next((request[key] for key in NUMBER_KEYS if request.get(key) is not None), None)
    if title:
        return f"the {noun} “{str(title)[:80]}”"
    if named:
        return f"{noun} {named}"
    if number is not None:
        return f"{noun} #{number}" if noun in {"issue", "pull request"} else f"a {noun} on #{number}"
    return f"the {noun}"


def _where(resource: str | None) -> str:
    return f" in {resource}" if resource else ""


def approval_wait(scope: str, request: Mapping[str, object], resource: str | None) -> str:
    """F10: 'Waiting for your approval to create branch audoryn/… in DIVINEJMG/school.'"""
    action = scope.rsplit(".", 1)[-1]
    template = "open {o} as a draft" if scope.endswith("pull_request.create") else TO_DO.get(action, f"{action} {{o}}")
    return f"Waiting for your approval to {template.format(o=write_object(scope, request))}{_where(resource)}."


def uncertain_write(scope: str, request: Mapping[str, object], resource: str | None) -> str:
    """F21: the write that may or may not have happened, where to look and what to answer."""
    action = scope.rsplit(".", 1)[-1]
    done = "opened" if scope.endswith("pull_request.create") else DONE.get(action, "made")
    place = resource or "the account"
    return (f"I couldn't confirm whether {write_object(scope, request)} was {done}{_where(resource)}. Please check "
            f"{place}, then tell me: {CHOICES}. {SAVED}")


PLANNING_PAUSED = {
    "content_rejected": "The AI planner declined to plan the next step, so I've paused this task.",
    "timeout": "The AI planner didn't answer in time, so I've paused this task.",
    "invalid_provider_response": "The AI planner's answers didn't follow the rules for a next step, so I've paused this task.",
}


def planning_paused(category: str) -> str:
    """F14: the task paused while planning, and how to continue."""
    reason = PLANNING_PAUSED.get(category, "Planning couldn't continue, so I've paused this task.")
    return f"{reason} {SAVED} Press Retry planning to ask again."


# A refusal's own words, and what it means for the person (F20).
REFUSALS = (
    ("Use this task's branch prefix",
     ("This task may only change its own new branches, so I didn't change that branch. To change an existing "
      "branch or pull request, ask me again and name it (for example “update pull request #8”).")),
    ("Direct writes to default/protected branches",
     ("I don't write directly to the default branch or a protected branch; changes go on a task branch and "
      "into a pull request.")),
    ("Fork mutation needs its own exact repository grant",
     "That pull request comes from a fork, which this task isn't allowed to change."),
)


def refused_write(reason: str) -> str:
    """F20: why the work stopped, in plain words with the way forward."""
    explanation = next((plain for start, plain in REFUSALS if reason.startswith(start)), None)
    return f"{explanation or 'I stopped: ' + reason.rstrip('.') + '.'} {SAVED}"
