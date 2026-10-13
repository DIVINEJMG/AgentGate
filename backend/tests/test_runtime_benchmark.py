"""The offline runtime benchmark (improvement plan, phase 0).

Every later phase must keep each task's outcome identical. Compute must match the recorded
baseline: a rise is a regression, and a fall is an improvement to record deliberately with
`python scripts/runtime_benchmark.py --write-baseline`, so the baseline only moves on purpose.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.runtime_benchmark import BASELINE, TASKS, run_all

# Bytes read include stored timings, which vary by a few bytes between runs.
BYTES_TOLERANCE = 0.01
COUNTS = ("qstash_messages", "deliveries", "idleDeliveries", "drains", "db_statements", "db_rows",
          "redis_commands", "ai_calls", "ai_tokens_in", "ai_tokens_out", "largestPromptChars",
          "qstashTotal", "dbStatementsTotal", "dbRowsTotal", "redisTotal")


@pytest.fixture(scope="module")
def results() -> dict[str, dict[str, Any]]:
    import asyncio

    return {result["task"]: json.loads(json.dumps(result, default=str)) for result in asyncio.run(run_all())}


@pytest.fixture(scope="module")
def baseline() -> dict[str, dict[str, Any]]:
    return {result["task"]: result for result in json.loads(BASELINE.read_text(encoding="utf-8"))}


def test_every_task_has_a_baseline(baseline: dict[str, Any]) -> None:
    assert sorted(baseline) == sorted(task.name for task in TASKS)


@pytest.mark.parametrize("task", [task.name for task in TASKS])
def test_outcome_is_unchanged(task: str, results: dict[str, Any], baseline: dict[str, Any]) -> None:
    assert results[task]["outcome"] == baseline[task]["outcome"]


@pytest.mark.parametrize("task", [task.name for task in TASKS])
def test_compute_matches_the_recorded_baseline(task: str, results: dict[str, Any], baseline: dict[str, Any]) -> None:
    actual, expected = results[task]["compute"], baseline[task]["compute"]
    changed = {name: (expected[name], actual[name]) for name in COUNTS if actual[name] != expected[name]}
    low, high = expected["dbBytesRead"] * (1 - BYTES_TOLERANCE), expected["dbBytesRead"] * (1 + BYTES_TOLERANCE)
    if not low <= actual["dbBytesRead"] <= high:
        changed["dbBytesRead"] = (expected["dbBytesRead"], actual["dbBytesRead"])
    assert not changed, (
        f"Compute changed (baseline, now): {changed}. If this is an intended improvement, record it with "
        "`python scripts/runtime_benchmark.py --write-baseline`."
    )


def test_the_tasks_cover_approval_and_policy_denial(results: dict[str, Any]) -> None:
    assert results["approved-write"]["outcome"]["approvals"] == ["approved"]
    denied = results["denied-write"]["outcome"]
    assert [call["operation"] for call in denied["providerCalls"]] == ["bench.record.read"]
    assert denied["result"]["status"] == "attention"
