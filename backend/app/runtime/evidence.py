"""Bounded, valid JSON evidence; truncation never implies complete inspection."""

from __future__ import annotations

import json


def bounded_evidence(value: object, limit: int = 24000) -> dict:
    omitted: list[str] = []

    def size(data):
        return len(json.dumps(data, ensure_ascii=False, default=str))

    def visit(data, budget, path, depth=0):
        if size(data) <= budget:
            return data
        if depth > 8 or budget < 32:
            omitted.append(path)
            return None
        if isinstance(data, dict):
            result = {}
            remaining = budget - 2
            # Preserve IDs, revisions, exit codes and pagination before bulky text.
            keys = sorted(
                data,
                key=lambda k: (
                    isinstance(data[k], (dict, list))
                    or isinstance(data[k], str)
                    and len(data[k]) > 500
                ),
            )
            for key in keys:
                cost = size(str(key)) + 4
                if remaining < cost + 32:
                    omitted.append(path + "." + str(key))
                    continue
                result[key] = visit(data[key], remaining - cost, path + "." + str(key), depth + 1)
                remaining -= cost + size(result[key])
            return result
        if isinstance(data, list):
            result = []
            remaining = budget - 2
            for index, entry in enumerate(data):
                if remaining < 32:
                    omitted.append(path + f"[{index}:]")
                    break
                result.append(visit(entry, remaining - 2, path + f"[{index}]", depth + 1))
                remaining -= size(result[-1]) + 2
            return result
        text = str(data)
        count = min(len(text), max(0, (budget - 4) // 2))
        omitted.append(path)
        return text[:count]

    data = visit(value, max(32, limit - 2000), "output")
    return {
        "data": data,
        "evidenceLimits": {
            "truncated": bool(omitted),
            "omittedPaths": omitted[:20],
            "limitChars": limit,
            "instruction": "Inspect omitted content with a narrower path, page or file segment. Repeating the identical full read does not recover omitted evidence."
            if omitted
            else "Complete within this observation.",
        },
    }


def planner_evidence(observations: list[dict[str, object]], *, relevant: set[int] | None = None) -> dict:
    """Keep every action identity separately from bounded, newest-first payloads.

    With a saved plan (relevant is the set of step numbers behind the current plan step and the
    steps it depends on), the full evidence is that set plus the latest two steps, and older
    history shrinks to one short line each, so the prompt stays small however long the run is."""
    history = []
    compact_before = len(observations) - 12 if relevant is not None else 0
    for index, item in enumerate(observations):
        if index < compact_before:
            entry = {key: item.get(key) for key in ("step", "scope", "status") if key in item}
            if item.get("summary"):
                entry["result"] = str(item.get("summary"))[:120]
            history.append(entry)
            continue
        entry = {key: item.get(key) for key in (
            "step", "scope", "resourceId", "actionFingerprint", "status"
        ) if key in item}
        verification = item.get("verification")
        if isinstance(verification, dict):
            entry["verified"] = verification.get("verified")
        entry["title"] = str(item.get("title") or "")[:180]
        # What the step found, so older steps (beyond the bounded recent evidence) are not forgotten.
        if item.get("summary"):
            entry["result"] = str(item.get("summary"))[:240]
        history.append(entry)
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
