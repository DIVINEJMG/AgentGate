"""Measure the simulated scenarios: the numbers every phase of the fix plan is compared with.

From tests/fixtures/sim/ (always): model requests, corrections and prompt sizes per scenario.
From the simulation database in `.env.sim` (when reachable): AI calls, QStash messages and
database statements per task, and statements per runtime step.

    python scripts/sim_metrics.py              prints a Markdown report
    python scripts/sim_metrics.py --json       prints the numbers as JSON
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND / "tests" / "fixtures" / "sim"


def from_fixtures() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for scenario in json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8")):
        rows = [json.loads(line) for line in
                (FIXTURES / "transcripts" / f"{scenario['id']}.jsonl").read_text(encoding="utf-8").splitlines() if line]
        by_kind: dict[str, list[int]] = {}
        for row in rows:
            by_kind.setdefault(row["schema_name"], []).append(row["promptChars"] + row["systemChars"])
        result[scenario["id"]] = {
            "workItem": scenario["workItem"], "modelRequests": len(rows),
            "corrections": sum(1 for row in rows if row["correction"]),
            "promptChars": {kind: {"max": max(sizes), "mean": round(sum(sizes) / len(sizes))}
                            for kind, sizes in by_kind.items()},
        }
    return result


async def from_database(work_items: list[str]) -> dict[str, dict[str, Any]]:
    import asyncpg

    env = dict(line.split("=", 1) for line in (BACKEND / ".env.sim").read_text(encoding="utf-8").splitlines()
               if "=" in line and not line.startswith("#"))
    connection = await asyncpg.connect(env["DATABASE_URL"].strip().split("?")[0] + "?sslmode=require")
    try:
        rows = await connection.fetch(
            """select left(work_item_id::text, 8) as item, kind,
                      count(*) as records, sum(ai_calls) as ai, sum(qstash_messages) as qstash,
                      sum(db_statements) as db, sum(duration_ms) as ms
               from usage_records where left(work_item_id::text, 8) = any($1::text[]) group by 1, 2""",
            work_items)
    finally:
        await connection.close()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = result.setdefault(row["item"], {"aiCalls": 0, "qstash": 0, "dbStatements": 0, "runtimeSteps": 0,
                                               "runtimeStepStatements": 0, "seconds": 0})
        item["aiCalls"] += row["ai"] or 0
        item["qstash"] += row["qstash"] or 0
        item["dbStatements"] += row["db"] or 0
        item["seconds"] += round((row["ms"] or 0) / 1000)
        if row["kind"] == "runtime.step":
            item["runtimeSteps"] += row["records"]
            item["runtimeStepStatements"] += row["db"] or 0
    for item in result.values():
        steps = item.pop("runtimeStepStatements")
        item["statementsPerStep"] = round(steps / item["runtimeSteps"]) if item["runtimeSteps"] else None
    return result


def report(fixtures: dict[str, dict[str, Any]], database: dict[str, dict[str, Any]]) -> str:
    lines = [("| Scenario | Model requests | Corrections | Largest prompt (chars) | AI calls (task) "
              "| QStash (task) | DB statements (task) | Statements per step |"),
             "|---|---|---|---|---|---|---|---|"]
    for name, item in fixtures.items():
        usage = database.get(item["workItem"] or "", {})
        largest = max((size["max"] for size in item["promptChars"].values()), default=0)
        lines.append(f"| {name} | {item['modelRequests']} | {item['corrections']} | {largest:,} | "
                     f"{usage.get('aiCalls', '—')} | {usage.get('qstash', '—')} | {usage.get('dbStatements', '—')} | "
                     f"{usage.get('statementsPerStep') or '—'} |")
    lines += ["", "| Prompt | Largest (chars) | Mean (chars) |", "|---|---|---|"]
    kinds: dict[str, list[int]] = {}
    means: dict[str, list[int]] = {}
    for item in fixtures.values():
        for kind, size in item["promptChars"].items():
            kinds.setdefault(kind, []).append(size["max"])
            means.setdefault(kind, []).append(size["mean"])
    for kind in sorted(kinds):
        lines.append(f"| {kind} | {max(kinds[kind]):,} | {round(sum(means[kind]) / len(means[kind])):,} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    fixtures = from_fixtures()
    work_items = [item["workItem"] for item in fixtures.values() if item["workItem"]]
    try:
        database = asyncio.run(from_database(work_items))
    except (OSError, KeyError) as error:
        print(f"(database figures unavailable: {type(error).__name__})")
        database = {}
    if args.json:
        print(json.dumps({"fixtures": fixtures, "database": database}, indent=1))
    else:
        print(report(fixtures, database))


if __name__ == "__main__":
    main()
