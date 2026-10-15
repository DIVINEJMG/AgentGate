"""Save the simulated scenarios as test fixtures (tests/fixtures/sim/).

For each scenario in SCENARIOS this writes:
  evidence/<scenario>/<step>-<scope>.json   the provider output of each action step (from the
                                            simulation database), for evidence and narration tests
  transcripts/<scenario>.jsonl              each model request's schema, prompt size and section
                                            sizes, and the answer given (no full prompts)
  scenarios.json                            the catalogue: messages, work items, expected outcome

Reads `.sim/log/` and the database named in `.env.sim`. Refuses to write anything that looks like
a credential. Run again after a new simulation to refresh.

    python scripts/export_sim_fixtures.py
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
LOG = BACKEND / ".sim" / "log"
OUT = BACKEND / "tests" / "fixtures" / "sim"

SCENARIOS: list[dict[str, Any]] = [
    {"id": "s1_open_issues", "messages": ["What are the open issues in DIVINEJMG/school"],
     "requests": [1, 6], "workItem": "181d53d6",
     "expected": "completed; reports open issue #2 'TE'; reads GitHub fresh",
     "findings": ["F1", "F2", "F3", "F4"]},
    {"id": "s2_create_issue",
     "messages": ["Create an issue in DIVINEJMG/school titled “Simulation test” saying this was created by a simulated run."],
     "requests": [7, 10], "workItem": "087ed8f1",
     "expected": "completed; one issue created (#7) with the requested title and body",
     "findings": ["F5", "F6", "Q1"]},
    {"id": "s3_coding_draft_pr", "humanSteps": ["approve opening the draft pull request (or --approve)"],
     "messages": ["In DIVINEJMG/school, add a README section about setup and a small helper script, then open a draft PR."],
     "requests": [11, 24], "workItem": "55ea0379",
     "expected": "completed; two workspace edits in one sandbox, task branch, commit, draft PR (#8) after approval",
     "findings": ["F5", "F7", "F8", "F9", "F10", "F11"]},
    {"id": "s4_bad_model",
     "humanSteps": ["send the second message after the first fails",
                    "press Retry planning in Run history after the refusal"],
     "messages": ["List All open Pull request in the DIVINEJMG/school", "List the open pull requests in DIVINEJMG/school."],
     "requests": [25, 32], "workItem": "496f0411",
     "expected": "nothing harmful runs; corrections name the problem; a refused task can be resumed",
     "findings": ["F12", "F13", "F14", "F15", "F16"]},
    {"id": "s5_clarification", "humanSteps": ["answer the clarification question with the second message"],
     "messages": ["Please merge the new branch into main.", "merge  codex/55ea0379/readme-setup in DIVINEJMG/school (draft pull request #8)."],
     "requests": [33, 37], "workItem": "1e81a73a",
     "expected": "a short clarification question; the answer continues the same request; the named PR can be merged after approval",
     "findings": ["F17", "F18", "F19", "F20"]},
    {"id": "s6_uncertain_write", "humanSteps": ["press Check again, then It didn't happen, on the Jobs page"],
     "messages": ["Create an issue in DIVINEJMG/school titled “Uncertain write test” saying it checks how AgentGate handles a lost write."],
     "requests": [38, 41], "workItem": "2ab06143",
     "faults": [{"method": "POST", "path_suffix": "/issues", "mode": "dropped", "times": 1}],
     "expected": "write held as dispatching; three automatic checks; pause with Check again / It didn't happen / It happened; one issue after 'It didn't happen'",
     "findings": ["F21"]},
    {"id": "s7_event_trigger",
     "humanSteps": ["answer the clarification with the second message",
                    "point the GitHub App webhook at the tunnel and open an issue"],
     "messages": ["When a new issue is opened in DIVINEJMG/school, summarise it here.", "keep that one"],
     "requests": [42, 48], "workItem": None,
     "expected": "no duplicate job; an issues.opened webhook runs the existing job once and posts one summary",
     "findings": ["F22", "F23", "F24"]},
]

SECRET = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY"
    r"|npg_[A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}|Bearer [A-Za-z0-9._-]{20,})"
)
SECTION = re.compile(r"^([A-Z][A-Z_ /()-]{3,}):?[ \t]*(?:\n|$)", re.MULTILINE)


def checked(text: str, where: str) -> str:
    match = SECRET.search(text)
    if match:
        raise SystemExit(f"Refusing to write {where}: it contains something that looks like a credential.")
    return text


def json_parts(name: str, body: str) -> dict[str, int]:
    """Characters per top-level key when a section's body (or a prompt with no headings) starts
    with one JSON object, so a single large context block is still measured by part."""
    text = body.lstrip()
    if not text.startswith("{"):
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {f"{name}.{key}": len(json.dumps(value, ensure_ascii=False)) for key, value in data.items()}


def section_sizes(prompt: str) -> dict[str, int]:
    """Characters per prompt section (the UPPER CASE headings AgentGate writes), and per top-level
    key of a JSON section."""
    marks = [(m.start(), m.end(), m.group(1).strip()) for m in SECTION.finditer(prompt)]
    sizes: dict[str, int] = {}
    if not marks or marks[0][0] > 0:
        head = prompt[: marks[0][0] if marks else len(prompt)]
        sizes["(before first section)"] = len(head)
        sizes.update(json_parts("(before first section)", head))
    for index, (start, body_start, name) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(prompt)
        sizes[name] = sizes.get(name, 0) + end - start
        sizes.update(json_parts(name, prompt[body_start:end]))
    return sizes


def transcript(first: int, last: int) -> list[dict[str, Any]]:
    rows = []
    for number in range(first, last + 1):
        path = LOG / f"{number:04d}.json"
        if not path.exists():
            continue
        entry = json.loads(path.read_text(encoding="utf-8"))
        request = entry["request"]
        system = str(request["messages"][0].get("content", "")) if len(request["messages"]) > 1 else ""
        prompt = str(request["messages"][-1].get("content", ""))
        rows.append({
            "number": number, "schema_name": request["schema_name"], "stream": request["stream"],
            "systemChars": len(system), "promptChars": len(prompt), "sections": section_sizes(prompt),
            "correction": "CORRECTION_REQUIRED" in prompt or "PREVIOUS DECISION WAS INVALID" in prompt
                          or "YOUR PREVIOUS RESPONSE WAS INVALID" in prompt,
            "answer": entry["answer"],
        })
    return rows


async def evidence(work_item: str) -> list[dict[str, Any]]:
    import asyncpg

    env = dict(line.split("=", 1) for line in (BACKEND / ".env.sim").read_text(encoding="utf-8").splitlines()
               if "=" in line and not line.startswith("#"))
    url = env["DATABASE_URL"].strip().split("?")[0] + "?sslmode=require"
    connection = await asyncpg.connect(url)
    try:
        rows = await connection.fetch(
            """select s.step_index, s.status, s.input->>'scope' as scope, s.input as input, s.output
               from run_steps s join runs r on r.id = s.run_id
               where r.work_item_id::text like $1 and s.kind = 'action' order by s.step_index""",
            work_item + "%")
    finally:
        await connection.close()
    return [{"step": row["step_index"], "status": row["status"], "scope": row["scope"],
             "input": json.loads(row["input"]) if row["input"] else None,
             "output": json.loads(row["output"]) if row["output"] else None} for row in rows]


async def main() -> None:
    (OUT / "transcripts").mkdir(parents=True, exist_ok=True)
    (OUT / "evidence").mkdir(parents=True, exist_ok=True)
    catalogue = []
    for scenario in SCENARIOS:
        rows = transcript(*scenario["requests"])
        path = OUT / "transcripts" / f"{scenario['id']}.jsonl"
        path.write_text(checked("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", str(path)),
                        encoding="utf-8")
        steps = await evidence(scenario["workItem"]) if scenario["workItem"] else []
        folder = OUT / "evidence" / scenario["id"]
        folder.mkdir(exist_ok=True)
        for step in steps:
            name = f"{step['step']:02d}-{(step['scope'] or 'step').split('.', 2)[-1]}.json"
            (folder / name).write_text(checked(json.dumps(step, indent=1, ensure_ascii=False), name), encoding="utf-8")
        largest: dict[str, int] = {}
        for row in rows:
            largest[row["schema_name"]] = max(largest.get(row["schema_name"], 0), row["promptChars"])
        catalogue.append({**scenario, "evidenceFiles": len(steps), "modelRequests": len(rows),
                          "corrections": sum(row["correction"] for row in rows), "largestPromptChars": largest})
        print(f"{scenario['id']}: {len(rows)} model requests, {len(steps)} evidence steps")
    (OUT / "scenarios.json").write_text(json.dumps(catalogue, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
