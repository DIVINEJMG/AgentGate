from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.application.migration_order import DOMAIN_MIGRATION_ORDER, assert_migration_order
from app.domain.integrations.contracts import IntegrationManifest, IntegrationOperation
from app.infrastructure.database.models import OutboxEvent

violations: list[str] = []

if len(DOMAIN_MIGRATION_ORDER) != 20:
    violations.append("F28.12 must define the full 20-domain migration order.")
try:
    assert_migration_order({"runtime"})
except ValueError:
    pass
else:
    violations.append("F28.12 migration order does not reject skipped prerequisites.")

manifest_fields = set(IntegrationManifest.__dataclass_fields__)
for required in {"provider", "resources", "capabilities", "credential_strategy", "operations"}:
    if required not in manifest_fields:
        violations.append(f"F28.11 IntegrationManifest missing {required}.")

operation_fields = set(IntegrationOperation.__dataclass_fields__)
for required in {"resource", "action", "scope", "risk", "input_schema", "output_schema", "side_effect"}:
    if required not in operation_fields:
        violations.append(f"F28.11 IntegrationOperation missing {required}.")

if OutboxEvent.__tablename__ != "outbox_events":
    violations.append("F28.15 canonical outbox table is missing.")

required_files = (
    "backend/app/infrastructure/database/outbox.py",
    "backend/app/runtime/outbox_worker.py",
    "backend/app/infrastructure/queue/broker.py",
    "backend/app/domain/secrets/vault.py",
    "backend/app/infrastructure/secrets/settings_vault.py",
    "backend/app/domain/artifacts/storage.py",
    "backend/app/observability/context.py",
    "backend/app/observability/middleware.py",
    "backend/app/runtime/worker.py",
    "backend/app/runtime/scheduler.py",
)
for relative in required_files:
    if not (ROOT / relative).exists():
        violations.append(f"F28.11-20 required boundary missing: {relative}")

# API process must not execute worker/scheduler loops.
api_root = BACKEND / "app" / "api"
for path in api_root.rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "app.runtime.worker",
            "app.runtime.scheduler",
        }:
            violations.append(f"F28.16 API imports runtime process loop: {path.relative_to(ROOT)}")

# Secrets must not be imported by the frontend.
frontend = ROOT / "frontend" / "src"
for path in list(frontend.rglob("*.ts")) + list(frontend.rglob("*.tsx")):
    text = path.read_text(encoding="utf-8").lower()
    for marker in ("qstash_token", "upstash_blob_token", "integration_encryption_key"):
        if marker in text:
            violations.append(f"F28.18 frontend exposes server secret marker {marker}: {path.relative_to(ROOT)}")

if violations:
    raise SystemExit("F28.11-F28.20 verification failed:\n" + "\n".join(f"- {v}" for v in violations))

print("F28.11-F28.20 architecture boundaries verified.")
