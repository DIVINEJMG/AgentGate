from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"

forbidden_prefixes = (
    "workers:",
    "jobs:",
    "results:",
    "policies:",
    "approvals:",
    "audit:",
    "capabilities:",
    "memberships:",
    "organizations:",
    "agents:",
)
violations: list[str] = []

for path in APP.rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    if "redis" not in text.lower():
        continue
    for prefix in forbidden_prefixes:
        if re.search(rf"""["']{re.escape(prefix)}""", text):
            violations.append(f"{path.relative_to(ROOT)} uses canonical Redis key prefix {prefix}")

if violations:
    raise SystemExit(
        "F28 Redis must remain coordination-only:\n"
        + "\n".join(f"- {item}" for item in violations)
    )

print("F28 Redis coordination-only boundary verified.")
