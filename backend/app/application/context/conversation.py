"""The conversation compacter: the workspace context a model reads to understand a message, as
references with a short description, never pasted bodies. Recent turns stay verbatim and older
ones shrink to a line, so the prompt does not grow with the thread. Used by interpretation and by
task preparation."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.application.context.evidence import as_list, as_map, map_of

RECENT_TURNS = 8
# Recent runs, results and work items shown; older ones stay reachable through their jobs.
RECENT_ITEMS = 6
TURN_CHARS = 2000
OLDER_TURN_CHARS = 160
# Policies AgentGate writes for each task's resource binding; shown as one count.
TASK_GRANT_PREFIX = "Integration binding "


def _clip(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _when(value: object) -> object:
    """An ISO time to the minute ("2026-10-15 13:07Z"): seconds and microseconds tell a model
    nothing, and every record carries one."""
    text = str(value)
    if len(text) >= 16 and text[10:11] == "T" and text.endswith(("+00:00", "Z")):
        return text[:10] + " " + text[11:16] + "Z"
    return value


TIMES = ("createdAt", "scheduledAt")


def _shown(record: Mapping[str, object], **limits: int | None) -> dict[str, Any]:
    """The named fields (clipped when a limit is given, times to the minute), leaving out empty ones."""
    shown = {key: _clip(record.get(key), limit) if limit else record.get(key) for key, limit in limits.items()}
    return {key: _when(value) if key in TIMES else value for key, value in shown.items()
            if value not in (None, "", [], {})}


def _policies(value: object) -> list[dict[str, Any]]:
    policies = [as_map(policy) for policy in as_list(value)]
    grants = sum(1 for policy in policies if str(policy.get("name") or "").startswith(TASK_GRANT_PREFIX))
    shown = [_shown(policy, id=None, name=200, effect=None, priority=None, description=300) for policy in policies
             if not str(policy.get("name") or "").startswith(TASK_GRANT_PREFIX)]
    if grants:
        shown.append({"name": f"{grants} resource grants made for earlier tasks", "effect": "allow"})
    return shown


def _jobs(value: object) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for job in map(as_map, as_list(value)):
        shown = _shown(job, id=None, workerId=None, name=200, status=None, runs=None)
        if shown.get("status") == "active":  # the list is of active jobs; only another status says something
            del shown["status"]
        if job.get("objective") and job.get("objective") != job.get("name"):
            shown["objective"] = _clip(job["objective"], 300)
        jobs.append(shown)
    return jobs


def _runs(value: object) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for run in map(as_map, as_list(value)[:RECENT_ITEMS]):
        steps = [as_map(step) for step in as_list(run.get("steps"))]
        shown = _shown(run, id=None, workItemId=None, status=None, summary=240, createdAt=None)
        if steps:
            shown["steps"] = len(steps)
            shown["lastSteps"] = [_clip(step.get("summary"), 160) for step in steps[-2:]]
        runs.append(shown)
    return runs


def _results(value: object) -> list[dict[str, Any]]:
    return [_shown(as_map(result), id=None, workerId=None, jobId=None, status=None, title=160, summary=300, createdAt=None)
            for result in as_list(value)[:RECENT_ITEMS]]


def _memories(value: object, results: object) -> list[dict[str, Any]]:
    """Memories, except those that only repeat a result listed beside them."""
    listed = {str(as_map(result).get("summary") or "") for result in as_list(results)}
    return [_shown(memory, id=None, type=None, title=120, content=300) for memory in map(as_map, as_list(value))
            if str(memory.get("content") or "") not in listed]


def _work(value: object) -> list[dict[str, Any]]:
    return [_shown(as_map(item), id=None, jobId=None, status=None, scheduledAt=None) for item in as_list(value)[:RECENT_ITEMS]]


def _turn(message: object, limit: int) -> object:
    """One turn: a message record's role, content and references, or a plain text turn."""
    if (record := map_of(message)) is None:
        return _clip(message, limit)
    return {**_shown(record, id=None, role=None, content=limit, createdAt=None),
            **{key: record[key] for key in ("artifactReferences", "resultReferences") if record.get(key)}}


def _older_turn(message: object) -> str | None:
    record = map_of(message)
    if record is None:
        return _clip(message, OLDER_TURN_CHARS)
    return f"{record.get('role')}: {_clip(record.get('content'), OLDER_TURN_CHARS)}"


def compact_thread(value: object, recent: int = RECENT_TURNS) -> dict[str, Any]:
    """Recent turns verbatim (bounded), older turns as one short line each."""
    thread = as_map(value)
    messages = as_list(thread.get("messages"))
    older, latest = (messages[:-recent], messages[-recent:]) if recent else (messages, [])
    compact: dict[str, Any] = {key: item for key, item in thread.items() if key != "messages"}
    if older:
        compact["earlierTurns"] = [_older_turn(message) for message in older]
    compact["messages"] = [_turn(message, TURN_CHARS) for message in latest]
    return compact


def _scopes(value: object) -> object:
    """Capability scopes as plain names rather than one record per scope; anything other than plain
    scope records is left as it is."""
    entries = [as_map(entry) for entry in as_list(value)]
    if not entries or any(set(entry) != {"scope"} for entry in entries):
        return value
    return [str(entry["scope"]) for entry in entries]


PROJECTIONS: dict[str, Callable[[object], object]] = {
    "policiesAndApprovalBoundaries": _policies,
    "activeJobs": _jobs,
    "recentRuns": _runs,
    "recentResults": _results,
    "currentWork": _work,
    "allowedCapabilities": _scopes,
    "thread": compact_thread,
}


def compact_context(context: Mapping[str, object]) -> dict[str, object]:
    """The context with each known part projected; unknown parts pass unchanged."""
    compact = {key: PROJECTIONS[key](value) if key in PROJECTIONS else value for key, value in context.items()}
    if "relevantMemories" in context:
        compact["relevantMemories"] = _memories(context["relevantMemories"], context.get("recentResults"))
    return _without_job_titles(_without_single_worker(compact))


def _without_job_titles(context: dict[str, object]) -> dict[str, object]:
    """A result titled after its job (listed beside it) repeats the job's name: its jobId says it."""
    names = {str(job.get("id")): str(job.get("name") or "") for job in map(as_map, as_list(context.get("activeJobs")))}
    for result in map(as_map, as_list(context.get("recentResults"))):
        name, title = names.get(str(result.get("jobId")), "").rstrip("…"), str(result.get("title") or "").rstrip("…")
        if name and title and (title.startswith(name) or name.startswith(title)):
            result.pop("title")
    return context


def _without_single_worker(context: dict[str, object]) -> dict[str, object]:
    """With one worker, its ID on every job and result says nothing."""
    rows = [as_map(row) for key in ("activeJobs", "recentResults") for row in as_list(context.get(key))]
    if len({row.get("workerId") for row in rows}) > 1:
        return context
    for row in rows:
        row.pop("workerId", None)
    return context
