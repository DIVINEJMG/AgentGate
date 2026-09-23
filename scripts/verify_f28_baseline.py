from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
FRONTEND_PACKAGE = (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")

required_python = (
    "fastapi",
    "pydantic",
    "sqlalchemy",
    "alembic",
    "redis",
    "pytest",
    "pytest-asyncio",
    "httpx",
    "ruff",
    "pyright",
)
missing = [name for name in required_python if name not in PYPROJECT.lower()]
if missing:
    raise SystemExit(f"F28 technology baseline missing Python dependencies: {missing}")

required_frontend = ("react", "typescript", "vite")
missing_frontend = [name for name in required_frontend if name not in FRONTEND_PACKAGE.lower()]
if missing_frontend:
    raise SystemExit(f"F28 technology baseline missing frontend dependencies: {missing_frontend}")

legacy_hits: list[str] = []
for path in (ROOT / "backend" / "app").rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    if re.search(r"(^|\s)(from|import)\s+@?appdeploy\b", text, re.MULTILINE):
        legacy_hits.append(str(path.relative_to(ROOT)))

if legacy_hits:
    raise SystemExit("Active Python runtime still imports AppDeploy: " + ", ".join(legacy_hits))

print("F28 technology baseline verified.")
