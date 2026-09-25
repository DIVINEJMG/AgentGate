from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

errors: list[str] = []

render = (ROOT / "infrastructure/render/render.yaml").read_text(encoding="utf-8")
internal = (ROOT / "backend/app/api/internal/runtime.py").read_text(encoding="utf-8")
managed = (ROOT / "backend/app/runtime/managed.py").read_text(encoding="utf-8")
jobs = (ROOT / "backend/app/api/jobs_routes.py").read_text(encoding="utf-8")
governance = (ROOT / "backend/app/api/governance_routes.py").read_text(encoding="utf-8")
settings = (ROOT / "backend/app/bootstrap/settings.py").read_text(encoding="utf-8")

if "name: audoryn-worker" in render:
    errors.append("paid Render runtime worker is still present")
if "RUNTIME_EXECUTION_ENABLED\n        value: \"true\"" not in render:
    errors.append("Render runtime execution is not enabled")
if "CUTOVER_STAGE\n        value: runtime" not in render:
    errors.append("Render cutover stage is not runtime")
for name in ("QSTASH_RUNTIME_EXECUTE_URL", "QSTASH_RUNTIME_SWEEP_URL"):
    if name not in render or name.lower() not in settings.lower():
        errors.append(f"missing runtime QStash setting: {name}")
for route in ('@router.post("/execute")', '@router.post("/sweep")'):
    if route not in internal:
        errors.append(f"missing protected runtime route: {route}")
if "RedisCoordinator" not in internal:
    errors.append("runtime execution lacks a Redis lease boundary")
if "ManagedRuntimeExecutor" not in internal:
    errors.append("QStash execute endpoint is not wired to Managed Runtime")
if "UniversalProviderExecutor" not in managed or "ActionGateway" not in managed:
    errors.append("Managed Runtime does not use universal governed execution")
if "UnconfiguredRuntimeExecutor" in managed:
    errors.append("Managed Runtime still uses the placeholder executor")
if "request_runtime_execution" not in jobs:
    errors.append("new WorkItems are not signaled to QStash")
if "reason=\"approval\"" not in governance:
    errors.append("approved Actions do not resume QStash runtime")

if errors:
    for error in errors:
        print(f"QStash runtime cutover failure: {error}", file=sys.stderr)
    raise SystemExit(1)

print("QStash managed runtime cutover verified.")
