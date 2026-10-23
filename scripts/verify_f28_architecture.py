from __future__ import annotations

import ast
from pathlib import Path
from code_paths import Code  # the backend's code, in its three packages (Phase 4)

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = Code("domain")

FORBIDDEN_PREFIXES = (
    "audoryn_shared.api",
    "audoryn_shared.infrastructure",
    "audoryn_shared.integrations",
    "fastapi",
    "sqlalchemy",
    "redis",
    "boto3",
)

violations: list[str] = []

for path in sorted(DOMAIN.rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        module = None
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(FORBIDDEN_PREFIXES):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: forbidden domain import {alias.name}"
                    )
            continue
        if module and module.startswith(FORBIDDEN_PREFIXES):
            violations.append(
                f"{path.relative_to(ROOT)}:{node.lineno}: forbidden domain import {module}"
            )

if violations:
    raise SystemExit(
        "F28 domain boundary violations:\n" + "\n".join(f"- {item}" for item in violations)
    )

print("F28 domain boundary check passed.")
