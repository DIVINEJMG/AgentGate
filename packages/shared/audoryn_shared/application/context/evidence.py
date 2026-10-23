"""What the planner is shown of each step's output.

Projections are shape-aware. Identities, state and content stay. Transport noise (API links,
whole account and repository objects, empty counters) goes. Whatever is cut is listed in
`evidenceLimits`, so a cut can never pass for complete evidence. Size-bounding is the final
guard for every shape.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

# Evidence is JSON: its readers index into it freely.
JSON = dict[str, Any]

TRUNCATION_INSTRUCTION = (
    "Inspect omitted content with a narrower path, page or file segment. Repeating the identical "
    "full read does not recover omitted evidence."
)
LIST_BODY_CHARS = 300
WRITTEN_CHARS = 600
# A list item that is an issue or pull request is shown by these fields.
ISSUE_FIELDS = ("number", "title", "state", "draft", "labels", "comments", "assignees", "user",
                "author", "body", "html_url", "webUrl", "updated_at", "updatedAt")
# A commit signature check is shown by its result, not the signature and signed text.
SIGNATURE_KEYS = frozenset({"signature", "payload"})
# A nested account or repository is shown by its name.
REFERENCE_KEYS = ("login", "full_name")
WORKSPACE_EXPORTS = (".workspace.diff", ".workspace.close")


def map_of(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def list_of(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None


def as_map(value: object) -> dict[str, object]:
    return map_of(value) or {}


def as_list(value: object) -> list[object]:
    return list_of(value) or []


def planned_action_fingerprint(scope: object, resource_id: object, action_input: object) -> str:
    """One action's identity (tool, resource and input), to recognise a repeat."""
    return hashlib.sha256(json.dumps({"scope": scope, "resource": resource_id, "input": action_input},
                                     sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def bounded_evidence(value: object, limit: int = 24000, *, cut: Sequence[str] = ()) -> JSON:
    """Valid JSON within `limit` characters. IDs, revisions, exit codes and pagination are kept
    before bulky text. `cut` lists paths already shortened upstream, so they are reported too."""
    omitted = list(cut)

    def visit(data: object, budget: int, path: str, depth: int = 0) -> object:
        if _size(data) <= budget:
            return data
        if depth > 8 or budget < 32:
            omitted.append(path)
            return None
        if (record := map_of(data)) is not None:
            return visit_map(record, budget, path, depth)
        if (items := list_of(data)) is not None:
            return visit_list(items, budget, path, depth)
        text = str(data)
        omitted.append(path)
        return text[: min(len(text), max(0, (budget - 4) // 2))]

    def visit_map(data: dict[str, object], budget: int, path: str, depth: int) -> dict[str, object]:
        result: dict[str, object] = {}
        remaining = budget - 2
        bulky = sorted(data, key=lambda key: isinstance(data[key], (dict, list))
                       or (isinstance(data[key], str) and len(str(data[key])) > 500))
        for key in bulky:
            cost = _size(key) + 4
            if remaining < cost + 32:
                omitted.append(f"{path}.{key}")
                continue
            result[key] = visit(data[key], remaining - cost, f"{path}.{key}", depth + 1)
            remaining -= cost + _size(result[key])
        return result

    def visit_list(data: list[object], budget: int, path: str, depth: int) -> list[object]:
        result: list[object] = []
        remaining = budget - 2
        for index, entry in enumerate(data):
            if remaining < 32:
                omitted.append(f"{path}[{index}:]")
                break
            result.append(visit(entry, remaining - 2, f"{path}[{index}]", depth + 1))
            remaining -= _size(result[-1]) + 2
        return result

    data = visit(value, max(32, limit - 2000), "output")
    return {
        "data": data,
        "evidenceLimits": {
            "truncated": bool(omitted),
            "omittedPaths": omitted[:20],
            "limitChars": limit,
            "instruction": TRUNCATION_INSTRUCTION if omitted else "Complete within this observation.",
        },
    }


def _clip(text: str, limit: int, path: str, cut: list[str]) -> str:
    if len(text) <= limit:
        return text
    cut.append(path)
    return text[:limit] + "…"


def _github_noise(key: str, value: object, record: Mapping[str, object]) -> bool:
    """API links, empty values and repeated links carry nothing a planner can use."""
    if value is None or key == "_links" or (key in SIGNATURE_KEYS and "verified" in record):
        return True
    if key == "html_url":
        return "webUrl" in record
    return isinstance(value, str) and (key == "url" or key.endswith("_url"))


def _github_value(value: object) -> object:
    if (items := list_of(value)) is not None:
        return [_github_value(item) for item in items]
    if (record := map_of(value)) is None:
        return value
    name = next((record[key] for key in REFERENCE_KEYS if isinstance(record.get(key), str)), None)
    return name if name is not None else _github_record(record)


def _github_record(record: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in record.items():
        if _github_noise(key, value, record):
            continue
        cleaned = ([as_map(label).get("name", label) for label in as_list(value)] if key == "labels"
                   else _github_value(value))
        if (shown := map_of(cleaned)) is not None and not any(shown.values()):
            continue  # an empty object or a summary of zeros
        result[key] = cleaned
    return result


def _github_list_item(item: object, path: str, cut: list[str]) -> object:
    record = as_map(item)
    if "number" not in record or "title" not in record:
        return _github_value(item)
    shown = _github_record({key: record[key] for key in ISSUE_FIELDS if key in record})
    if isinstance(body := shown.get("body"), str):
        shown["body"] = _clip(body, LIST_BODY_CHARS, f"{path}.body", cut)
    return shown


def _workspace_changes(changes: object) -> list[object]:
    """Changed files by path and size; their content is in the diff beside them."""
    return [{"path": change.get("path"), "deleted": True} if change.get("delete")
            else {"path": change.get("path"), "chars": len(str(change.get("content") or ""))}
            for change in map(as_map, as_list(changes))]


def compact_output(scope: str, output: object, *, action_input: Mapping[str, object] | None = None,
                   confirmed_fields: object = None, limit: int = 24000) -> JSON:
    """The planner's view of one provider output, as {"data", "evidenceLimits"}.

    A list keeps every item (GitHub issues and pull requests as short records). A write adds the
    requested values its provider re-read and confirmed (`confirmedWritten`), so a criterion
    about what was written can be proved from evidence alone."""
    github = scope.startswith("github.")
    cut: list[str] = []
    data: dict[str, object]
    if (items := list_of(output)) is not None:
        data = {"count": len(items), "items": [
            _github_list_item(item, f"output.items[{index}]", cut) for index, item in enumerate(items)
        ] if github else items}
    elif (record := map_of(output)) is not None:
        data = _github_record(record) if github else record
        if scope.endswith(WORKSPACE_EXPORTS) and "changes" in data:
            data["changes"] = _workspace_changes(data["changes"])
    else:
        data = {} if output is None else {"value": output}
    requested = action_input or {}
    written = {str(field): _clip(str(requested[str(field)]), WRITTEN_CHARS, f"confirmedWritten.{field}", cut)
               for field in as_list(confirmed_fields) if str(field) in requested}
    if written:
        data["confirmedWritten"] = written
    return bounded_evidence(data, limit, cut=cut)


ELEMENT_FLAGS = ("ref", "tag", "role", "element_type", "field_name", "checked", "disabled",
                 "keyboard_enter_safe")
ELEMENT_TEXT = (("name", 240), ("text", 240), ("href", 320), ("value", 240))
FORM_CONTROL_TAGS = frozenset({"input", "textarea", "select", "button"})
FORM_CONTROL_ROLES = frozenset({"textbox", "checkbox", "combobox", "button"})
FORM_CONTROL_TYPES = frozenset({"text", "email", "checkbox", "submit"})
ACTION_EVIDENCE_KEYS = ("operation", "result", "outcome", "verification", "stateChanged",
                        "elementReference", "beforeObservationId", "afterObservationId")


def _element_rank(element: Mapping[str, object], form_refs: set[str]) -> tuple[int, str]:
    """Form fields first, then other controls, then the rest; by ref within each."""
    ref = str(element.get("ref") or "")
    control = (str(element.get("tag") or "").lower() in FORM_CONTROL_TAGS
               or str(element.get("role") or "").lower() in FORM_CONTROL_ROLES
               or str(element.get("element_type") or "").lower() in FORM_CONTROL_TYPES)
    return (0 if ref in form_refs else 1 if control else 2), ref


def _element(element: Mapping[str, object]) -> dict[str, object]:
    shown = {key: element.get(key) for key in ELEMENT_FLAGS if key in element}
    shown.update({key: str(element[key])[:limit] for key, limit in ELEMENT_TEXT
                  if element.get(key) is not None})
    return shown


def browser_evidence(observation: Mapping[str, object], action_evidence: object) -> JSON:
    """A browser observation for the planner: up to 100 elements, form controls first, and
    bounded page text."""
    forms = as_list(observation.get("formDetails"))
    form_refs = {str(ref) for form in map(as_map, forms) for key in ("fieldRefs", "submitRefs")
                 for ref in as_list(form.get(key))}
    elements = sorted((element for element in map(map_of, as_list(observation.get("elements")))
                       if element is not None), key=lambda element: _element_rank(element, form_refs))
    evidence = as_map(action_evidence)
    return {
        "id": observation.get("id"),
        "sessionId": observation.get("sessionId"),
        "url": observation.get("url"),
        "title": observation.get("title"),
        "formDetails": forms,
        "elements": [_element(element) for element in elements[:100]],
        "pageState": observation.get("pageState", {}),
        "actionEvidence": {key: evidence.get(key) for key in ACTION_EVIDENCE_KEYS} if evidence else {},
        "visibleText": str(observation.get("visibleText") or "")[:12000],
        "ariaSnapshot": str(observation.get("ariaSnapshot") or "")[:2500],
    }


def planner_evidence(observations: list[dict[str, object]], *, relevant: set[int] | None = None) -> JSON:
    """Keep every action identity separately from bounded, newest-first payloads.

    With a saved plan (relevant is the set of step numbers behind the current plan step and the
    steps it depends on), the full evidence is that set plus the latest two steps, and older
    history shrinks to one short line each, so the prompt stays small however long the run is."""
    compact_before = len(observations) - 12 if relevant is not None else 0
    history = [_history_line(item, short=index < compact_before) for index, item in enumerate(observations)]
    if relevant is None:
        recent = list(reversed(observations[-6:]))
    else:
        chosen = [item for item in observations if item.get("step") in relevant][-4:]
        latest = [item for item in observations[-2:] if item not in chosen]
        recent = list(reversed([*chosen, *latest]))
    allowance = max(1000, 16000 // max(1, len(recent)))
    return {
        "actionHistory": history,
        "recentEvidence": [bounded_evidence(item, allowance) for item in recent],
        "instruction": "History includes every recorded action. Evidence is newest first and bounded; inspect omitted content with narrower reads, not an identical full read.",
    }


def _history_line(item: Mapping[str, object], *, short: bool) -> dict[str, object]:
    keys = ("step", "scope", "status") if short else ("step", "scope", "resourceId", "actionFingerprint", "status")
    entry = {key: item.get(key) for key in keys if key in item}
    if not short:
        if (verification := map_of(item.get("verification"))) is not None:
            entry["verified"] = verification.get("verified")
        entry["title"] = str(item.get("title") or "")[:180]
    # What the step found, so older steps (beyond the bounded recent evidence) are not forgotten.
    if item.get("summary"):
        entry["result"] = str(item.get("summary"))[: 120 if short else 240]
    return entry
