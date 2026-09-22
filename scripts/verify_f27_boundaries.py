from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DOMAIN = BACKEND / "app" / "domain"

errors: list[str] = []

for path in DOMAIN.rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    if re.search(r"(^|\\n)\\s*(from|import)\\s+.*(upstash|qstash)", text, re.I):
        errors.append(f"provider-specific import in domain: {path.relative_to(ROOT)}")

secret_names = (
    "DATABASE_URL", "REDIS_URL", "UPSTASH_QSTASH_TOKEN", "QSTASH_TOKEN",
    "UPSTASH_BLOB_TOKEN", "INTEGRATION_ENCRYPTION_KEY", "MODEL_PROVIDER_API_KEY",
)
for path in FRONTEND.rglob("*"):
    if not path.is_file() or "node_modules" in path.parts:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for name in secret_names:
        if name in text:
            errors.append(f"backend secret name {name} referenced by {path.relative_to(ROOT)}")

proc = subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
    cwd=BACKEND, check=False, capture_output=True, text=True,
)
if proc.returncode:
    errors.append("fresh Alembic SQL generation failed")
else:
    creates = re.findall(r"CREATE TABLE(?: IF NOT EXISTS)? queue_delivery_failures", proc.stdout, re.I)
    if len(creates) != 2:
        errors.append(f"unexpected queue_delivery_failures migration shape: {len(creates)} creates")
    if "CREATE TABLE IF NOT EXISTS queue_delivery_failures" not in proc.stdout:
        errors.append("0004 queue delivery migration is not fresh-install safe")

if errors:
    for error in errors:
        print(f"F27 boundary failure: {error}", file=sys.stderr)
    raise SystemExit(1)

print("F27 provider boundaries verified.")
