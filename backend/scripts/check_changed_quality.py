"""Quality guard for changed code: functions a branch touches must stay small and simple.

Checks only functions whose lines a change touches (new files count as touched throughout), so
legacy code is improved where it is fixed instead of in one large rewrite. In `app/` and
`scripts/` (tests are exempt), a touched function:
- fails over FAIL_LINES lines, warns over WARN_LINES;
- must pass ruff's complexity (C901), branch (PLR0912) and statement (PLR0915) rules;
- may not contain a block duplicated elsewhere, a database call inside a loop, or the same
  value serialised twice (scripts/code_health.py);
- warns when it writes out by hand a call that appears in 3+ places (a missing helper).

    python scripts/check_changed_quality.py                 compare with origin/main
    python scripts/check_changed_quality.py --base main
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CHECKED = ("backend/app/", "backend/scripts/")
WARN_LINES, FAIL_LINES = 80, 150
RUFF_RULES = "C901,PLR0912,PLR0915"
RUFF_SETTINGS = ("lint.mccabe.max-complexity=15", "lint.pylint.max-branches=15", "lint.pylint.max-statements=60")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=check)


def git(*args: str) -> str:
    return run(["git", *args]).stdout


def changed_lines(base: str) -> dict[str, set[int] | None]:
    """Changed Python files and their changed line numbers (None: a new file, all of it)."""
    merge_base = git("merge-base", base, "HEAD").strip()
    result: dict[str, set[int] | None] = {}
    tracked = git("diff", "--name-only", "--diff-filter=AMR", merge_base).split()
    untracked = git("ls-files", "--others", "--exclude-standard").split()
    for path in {*tracked, *untracked}:
        if not path.endswith(".py") or not path.startswith(CHECKED):
            continue
        existed = run(["git", "cat-file", "-e", f"{merge_base}:{path}"], check=False).returncode == 0
        if path in untracked or not existed:
            result[path] = None
            continue
        lines: set[int] = set()
        for start, count in HUNK.findall(git("diff", "-U0", merge_base, "--", path)):
            first, size = int(start), int(count) if count else 1
            lines.update(range(first, first + size))
        result[path] = lines
    return result


def touched_functions(path: str, lines: set[int] | None) -> list[tuple[str, int, int]]:
    """(name, first line, last line) of each outermost-or-nested function the change touches."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            if lines is None or any(node.lineno <= line <= end for line in lines):
                found.append((node.name, node.lineno, end))
    return found


def ruff_findings(paths: list[str]) -> list[dict]:
    command = [sys.executable, "-m", "ruff", "check", "--select", RUFF_RULES, "--output-format", "json",
               "--no-cache", *[arg for setting in RUFF_SETTINGS for arg in ("--config", setting)], *paths]
    return json.loads(run(command, check=False).stdout or "[]")


Spans = dict[str, list[tuple[str, int, int]]]


def inside(spans: Spans, path: str, row: int) -> bool:
    return any(first <= row <= last for _, first, last in spans.get(path, []))


def size_findings(spans: Spans) -> tuple[list[str], list[str]]:
    failures, warnings = [], []
    for path, functions in spans.items():
        for name, first, last in functions:
            size = last - first + 1
            if size > FAIL_LINES:
                failures.append(f"{path}:{first} {name} is {size} lines (limit {FAIL_LINES}); split it while it is being changed")
            elif size > WARN_LINES:
                warnings.append(f"{path}:{first} {name} is {size} lines (aim for {WARN_LINES} or fewer)")
    return failures, warnings


def lint_findings(spans: Spans) -> list[str]:
    failures = []
    for finding in ruff_findings(sorted(spans)):
        path = Path(finding["filename"]).resolve().relative_to(REPO.resolve()).as_posix()
        if inside(spans, path, finding["location"]["row"]):
            failures.append(f"{path}:{finding['location']['row']} {finding['code']} {finding['message']}")
    return failures


def craft_findings(spans: Spans) -> tuple[list[str], list[str]]:
    """Duplicated blocks, database calls in loops and repeated serialisation inside touched
    functions fail; a touched call written out by hand in 3+ places warns (a missing helper)."""
    import ast as syntax

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from scripts import code_health

    files = {path.relative_to(BACKEND): syntax.parse(path.read_text(encoding="utf-8"))
             for root in ("app", "scripts") for path in sorted((BACKEND / root).rglob("*.py"))}

    def repo(path: str) -> str:
        return "backend/" + path

    failures, warnings = [], []
    for group in code_health.duplicates(files):
        for path, first, _ in group["places"]:
            if inside(spans, repo(path), first):
                others = "; ".join(f"{p}:{a}" for p, a, _ in group["places"] if (p, a) != (path, first))
                failures.append(f"{repo(path)}:{first} duplicates {others}; share one helper")
    for path, line, call in code_health.loop_queries(files):
        if inside(spans, repo(path), line):
            failures.append(f"{repo(path)}:{line} database call `{call}` inside a loop; load in one query")
    for path, line, expr, count in code_health.repeated_dumps(files):
        if inside(spans, repo(path), line):
            failures.append(f"{repo(path)}:{line} json.dumps({expr}) is computed {count} times; compute it once")
    for shape in code_health.repeated_call_shapes(files):
        for place in shape["places"]:
            path, line = place.rsplit(":", 1)
            if inside(spans, repo(path), int(line)):
                warnings.append(f"{repo(path)}:{line} {shape['call']}(...) is written out by hand "
                                f"{shape['uses']} times; consider one helper")
    return failures, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    changes = changed_lines(args.base)
    spans: Spans = {path: touched_functions(path, lines) for path, lines in sorted(changes.items())}
    size_failures, warnings = size_findings(spans)
    craft_failures, craft_warnings = craft_findings(spans)
    failures = size_failures + lint_findings(spans) + craft_failures
    warnings += craft_warnings
    for line in warnings:
        print("warning:", line)
    for line in failures:
        print("error:", line)
    print(f"{len(changes)} changed file(s), {sum(len(v) for v in spans.values())} touched function(s), "
          f"{len(failures)} error(s), {len(warnings)} warning(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
