from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.bootstrap.application import create_application  # noqa: E402

application = create_application()
paths = set(application.openapi().get("paths", {}))

frontend_text = "\n".join(
    path.read_text(encoding="utf-8")
    for path in (ROOT / "frontend" / "src").rglob("*.ts")
)
frontend_text += "\n" + "\n".join(
    path.read_text(encoding="utf-8")
    for path in (ROOT / "frontend" / "src").rglob("*.tsx")
)

versioned = re.compile(
    r"/api/\$\{version\}(/[^\`'\"?]+)"
)
violations: list[str] = []

for suffix in sorted(set(versioned.findall(frontend_text))):
    canonical = re.sub(r"\$\{[^}]+\}", "{param}", suffix)
    python_v1 = "/api/v1" + canonical
    python_v2 = "/api/v2" + canonical

    def matches(pattern: str) -> bool:
        regex = "^" + re.escape(pattern).replace(re.escape("{param}"), r"[^/]+") + "$"
        return any(re.match(regex, path) for path in paths)

    has_v1 = matches(python_v1)
    has_v2 = matches(python_v2)
    if has_v1 != has_v2:
        violations.append(
            f"frontend contract {suffix} is only partially migrated: "
            f"v1={has_v1}, v2={has_v2}"
        )

if violations:
    raise SystemExit(
        "F28 v1/v2 parity violations:\n"
        + "\n".join(f"- {item}" for item in violations)
    )

print("F28 v1/v2 paired-migration gate verified.")
