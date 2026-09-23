from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = (
    ROOT / "backend" / "app" / "api",
    ROOT / "backend" / "app" / "application",
    ROOT / "backend" / "app" / "domain",
)
FORBIDDEN_PROVIDER_IMPORTS = (
    "github",
    "googleapiclient",
    "google.auth",
    "slack_sdk",
    "boto3",
    "botocore",
    "requests",
)
violations: list[str] = []

for root in ACTIVE:
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(FORBIDDEN_PROVIDER_IMPORTS):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} imports provider SDK {name}"
                    )

gateway = (ROOT / "backend" / "app" / "domain" / "actions" / "gateway.py").read_text(
    encoding="utf-8"
)
for invariant in (
    "Human identity cannot authenticate as Agent.",
    "Side effects require a stable idempotency key.",
    "Cross-tenant action denied.",
    "Agent identity mismatch.",
    "Agent lacks declared capability.",
    "REQUIRE_APPROVAL",
):
    if invariant not in gateway:
        violations.append(f"ActionGateway missing invariant: {invariant}")

if violations:
    raise SystemExit(
        "F28 Action Gateway boundary violations:\n"
        + "\n".join(f"- {item}" for item in violations)
    )

print("F28 Action Gateway boundary verified.")
