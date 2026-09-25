from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

ai_paths = [
    ROOT / "backend/app/domain/ai",
    ROOT / "backend/app/infrastructure/ai",
]
ai_files = [ROOT / "backend/app/application/services/ai_gateway.py"]
for directory in ai_paths:
    ai_files.extend(sorted(directory.glob("*.py")))

for path in ai_files:
    text = path.read_text(encoding="utf-8").lower()
    for forbidden_import in (
        "app.integrations",
        "app.execution",
        "app.runtime",
        "app.infrastructure.qstash",
    ):
        if forbidden_import in text:
            errors.append(
                f"{path.relative_to(ROOT)} crosses AI/side-effect boundary via {forbidden_import}"
            )

runtime_files = list((ROOT / "backend/app/runtime").rglob("*.py"))
for path in runtime_files:
    text = path.read_text(encoding="utf-8").lower()
    for vendor in ("openai", "nvidianimprovider", "integrate.api.nvidia.com"):
        if vendor in text:
            errors.append(
                f"{path.relative_to(ROOT)} contains vendor-specific AI dependency: {vendor}"
            )

managed = (ROOT / "backend/app/runtime/managed.py").read_text(encoding="utf-8")
if "ActionGateway" not in managed or "UniversalProviderExecutor" not in managed:
    errors.append("Managed Runtime no longer preserves F29 governed execution")
if 'role="planner"' not in (
    ROOT / "backend/app/runtime/planner/adaptive.py"
).read_text(encoding="utf-8"):
    errors.append("Adaptive planner is not routed through the planner model role")

if errors:
    for error in errors:
        print(f"AI provider boundary failure: {error}", file=sys.stderr)
    raise SystemExit(1)

print("AI provider and side-effect authority boundary verified.")
