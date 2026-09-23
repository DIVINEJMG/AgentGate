from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

required = [
    "compose.yaml",
    ".env.example",
    "backend/app/application/services/data_migration.py",
    "backend/app/application/services/cutover_gate.py",
    "backend/app/application/services/shadow_mode.py",
    "docs/f28-checkpoints-21-26.md",
    "docs/f26-rollback.md",
]

missing = [path for path in required if not (ROOT / path).exists()]
if missing:
    raise SystemExit("F28.21-F28.26 missing required files: " + ", ".join(missing))

compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
for service in ("postgres:", "redis:", "minio:"):
    if service not in compose:
        raise SystemExit(f"F28.21 local development missing {service}")

ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
for gate in (
    "ruff format --check",
    "pytest tests/parity",
    "pytest tests/contract",
    "verify_f28_security_invariants.py",
    "verify_f28_21_26.py",
):
    if gate not in ci:
        raise SystemExit(f"F28.22 CI gate missing: {gate}")

settings = (ROOT / "backend/app/bootstrap/settings.py").read_text(encoding="utf-8")
for flag in ("shadow_mode_enabled", "cutover_stage", "runtime_execution_enabled"):
    if flag not in settings:
        raise SystemExit(f"F28 cutover setting missing: {flag}")

print("F28.21-F28.26 migration and cutover gates verified.")
