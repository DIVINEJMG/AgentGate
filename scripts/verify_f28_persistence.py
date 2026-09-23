from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.infrastructure.database import models  # noqa: F401,E402
from app.infrastructure.database.base import Base  # noqa: E402

required_tables = {
    "organizations",
    "organization_memberships",
    "human_identities",
    "local_auth_credentials",
    "agent_identities",
    "agent_credentials",
    "workers",
    "workforce_roles",
    "jobs",
    "job_revisions",
    "work_items",
    "runs",
    "run_steps",
    "integrations",
    "integration_credentials",
    "capability_profiles",
    "policies",
    "policy_revisions",
    "actions",
    "approvals",
    "risk_events",
    "incidents",
    "execution_controls",
    "memories",
    "artifacts",
    "results",
    "result_versions",
    "result_exports",
    "audit_events",
    "outbox_events",
}
actual = set(Base.metadata.tables)
missing = sorted(required_tables - actual)
if missing:
    raise SystemExit(f"F28 Neon canonical schema missing tables: {missing}")

required_fks = {
    ("organization_memberships", "organization_id", "organizations.id"),
    ("organization_memberships", "user_id", "human_identities.id"),
    ("agent_identities", "organization_id", "organizations.id"),
    ("workers", "agent_identity_id", "agent_identities.id"),
    ("jobs", "worker_id", "workers.id"),
    ("work_items", "job_id", "jobs.id"),
    ("runs", "work_item_id", "work_items.id"),
    ("approvals", "action_id", "actions.id"),
    ("results", "worker_id", "workers.id"),
    ("results", "job_id", "jobs.id"),
}
found: set[tuple[str, str, str]] = set()
for table in Base.metadata.tables.values():
    for column in table.columns:
        for fk in column.foreign_keys:
            found.add((table.name, column.name, fk.target_fullname))
missing_fks = sorted(required_fks - found)
if missing_fks:
    raise SystemExit(f"F28 tenant/lineage constraints missing: {missing_fks}")

print("F28 Neon canonical persistence schema verified.")
