"""Code health beyond lint: duplicated logic, dead code and wasteful patterns.

- **Duplicated blocks:** runs of MIN_STATEMENTS or more consecutive statements that are the same
  once names, strings and numbers are normalised. Copied logic, even with renamed variables, is
  found.
- **Dead functions:** module-level functions and methods whose name appears nowhere else in the
  backend (tests and scripts count as uses). A starting list for review: dynamic use (getattr,
  routes) can hide a caller.
- **Queries in loops:** `await session.scalar/execute/get/scalars` inside a `for` loop over a
  collection, the usual source of N+1 database round trips. (A `while` loop that polls once per
  round is not N+1.)
- **Repeated serialisation:** `json.dumps` of the same expression more than once in one function.

    python scripts/code_health.py                    the whole backend app
    python scripts/code_health.py app/runtime        one folder
    python scripts/code_health.py --json
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
MIN_STATEMENTS = 5
MIN_LINES = 8
QUERY_CALLS = {"scalar", "scalars", "execute", "get", "fetch", "fetchrow", "fetchval"}


class Normalise(ast.NodeTransformer):
    """Same shape, different names or literals: the same code."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        return ast.copy_location(ast.arg(arg="_", annotation=None), node)

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)


def fingerprint(statements: list[ast.stmt]) -> str:
    module = ast.Module(body=[Normalise().visit(ast.parse(ast.unparse(s)).body[0]) for s in statements],
                        type_ignores=[])
    return hashlib.sha1(ast.dump(module, annotate_fields=False).encode(), usedforsecurity=False).hexdigest()


def statement_runs(tree: ast.AST):
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and len(block) >= MIN_STATEMENTS and all(isinstance(s, ast.stmt) for s in block):
                yield block


def duplicates(files: dict[Path, ast.Module]) -> list[dict[str, Any]]:
    seen: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for path, tree in files.items():
        for block in statement_runs(tree):
            for start in range(len(block) - MIN_STATEMENTS + 1):
                window = block[start:start + MIN_STATEMENTS]
                first, last = window[0].lineno, window[-1].end_lineno or window[-1].lineno
                if last - first + 1 >= MIN_LINES:
                    seen[fingerprint(window)].append((path.as_posix(), first, last))
    groups = []
    for places in seen.values():
        distinct = sorted(set(places))
        if len(distinct) > 1:
            groups.append({"places": distinct, "lines": distinct[0][2] - distinct[0][1] + 1})
    # Keep one group per overlapping region: the longest windows are reported, not every shift.
    groups.sort(key=lambda g: (-len(g["places"]), -g["lines"]))
    reported, covered = [], set()
    for group in groups:
        key = tuple((p, first // 10) for p, first, _ in group["places"])
        if key not in covered:
            covered.add(key)
            reported.append(group)
    return reported


def defined_functions(files: dict[Path, ast.Module]) -> list[tuple[str, str, int]]:
    found = []
    for path, tree in files.items():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("__"):
                if any(isinstance(d, (ast.Name, ast.Attribute, ast.Call)) for d in node.decorator_list):
                    continue  # routes, fixtures and registered handlers are called by a framework
                found.append((node.name, path.as_posix(), node.lineno))
    return found


def dead_functions(files: dict[Path, ast.Module]) -> list[tuple[str, str, int]]:
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in BACKEND.rglob("*.py") if ".venv" not in p.parts)
    counts: dict[str, int] = defaultdict(int)
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", corpus):
        counts[word] += 1
    return [(name, path, line) for name, path, line in defined_functions(files) if counts[name] <= 1]


def loop_queries(files: dict[Path, ast.Module]) -> list[tuple[str, int, str]]:
    found = []
    for path, tree in files.items():
        for loop in ast.walk(tree):
            if not isinstance(loop, (ast.For, ast.AsyncFor)):
                continue
            for node in ast.walk(loop):
                if (isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Attribute) and node.value.func.attr in QUERY_CALLS):
                    found.append((path.as_posix(), node.lineno, node.value.func.attr))
    return sorted(set(found))


def repeated_call_shapes(files: dict[Path, ast.Module], minimum_keywords: int = 4,
                         minimum_uses: int = 3) -> list[dict[str, Any]]:
    """Calls to the same function with the same set of 4+ keyword arguments, written out 3+ times:
    a missing helper (the same object rebuilt by hand in several places)."""
    shapes: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for path, tree in files.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and len(node.keywords) >= minimum_keywords:
                name = ast.unparse(node.func).rsplit(".", 1)[-1]
                keys = tuple(sorted(k.arg or "**" for k in node.keywords))
                shapes[(name, keys)].append(f"{path.as_posix()}:{node.lineno}")
    found = [{"call": name, "keywords": list(keys), "uses": len(places), "places": places}
             for (name, keys), places in shapes.items() if len(places) >= minimum_uses]
    return sorted(found, key=lambda shape: -shape["uses"])


def repeated_dumps(files: dict[Path, ast.Module]) -> list[tuple[str, int, str, int]]:
    found = []
    for path, tree in files.items():
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            counts: dict[str, int] = defaultdict(int)
            for node in ast.walk(function):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "dumps"
                        and node.args):
                    counts[ast.unparse(node.args[0])] += 1
            found += [(path.as_posix(), function.lineno, expr[:60], n) for expr, n in counts.items() if n > 1]
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", default=["app"])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()
    files: dict[Path, ast.Module] = {}
    for root in args.paths:
        for path in sorted((BACKEND / root).rglob("*.py")):
            files[path.relative_to(BACKEND)] = ast.parse(path.read_text(encoding="utf-8"))
    report = {"files": len(files), "duplicateGroups": duplicates(files), "deadFunctions": dead_functions(files),
              "queriesInLoops": loop_queries(files), "repeatedDumps": repeated_dumps(files),
              "repeatedCallShapes": repeated_call_shapes(files)}
    if args.json:
        print(json.dumps(report, indent=1))
        return
    print(f"{report['files']} files")
    print(f"\nDuplicated blocks: {len(report['duplicateGroups'])} groups")
    for group in report["duplicateGroups"][: args.top]:
        print(f"  {group['lines']} lines x{len(group['places'])}: "
              + "; ".join(f"{p}:{a}" for p, a, _ in group["places"][:4]))
    print(f"\nPossibly dead functions: {len(report['deadFunctions'])}")
    for name, path, line in report["deadFunctions"][: args.top]:
        print(f"  {path}:{line} {name}")
    print(f"\nDatabase calls inside loops: {len(report['queriesInLoops'])}")
    for path, line, call in report["queriesInLoops"][: args.top]:
        print(f"  {path}:{line} {call}")
    print(f"\nSame call written out by hand 3+ times (4+ keyword arguments): {len(report['repeatedCallShapes'])}")
    for shape in report["repeatedCallShapes"][: args.top]:
        print(f"  {shape['call']}({', '.join(shape['keywords'][:6])}) x{shape['uses']}: {shape['places'][0]}")
    print(f"\nSame value serialised more than once in a function: {len(report['repeatedDumps'])}")
    for path, line, expr, n in report["repeatedDumps"][: args.top]:
        print(f"  {path}:{line} json.dumps({expr}) x{n}")


if __name__ == "__main__":
    main()
