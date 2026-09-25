from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


draft_generator = read("backend/app/application/services/worker_draft.py")
resolver = read("backend/app/application/services/capability_autoresolver.py")
autonomy = read("backend/app/application/services/worker_autonomy.py")
attachments = read("backend/app/application/services/attachment_ingestion.py")
managed = read("backend/app/runtime/managed.py")

for path, text in (
    ("worker_draft.py", draft_generator),
    ("capability_autoresolver.py", resolver),
    ("worker_autonomy.py", autonomy),
    ("attachment_ingestion.py", attachments),
):
    for forbidden in (
        "IntegrationCredential.ciphertext",
        "decrypt_integration_secret",
        "DatabaseSecretVault",
        "AgentCredential.secret_hash",
    ):
        if forbidden in text:
            errors.append(f"{path} crosses the secret-provider boundary via {forbidden}")

for forbidden in (
    "ActionGateway(",
    "UniversalProviderExecutor(",
    ".execute_request(",
):
    if forbidden in autonomy:
        errors.append(
            f"AI worker creation directly executes side-effect provider code: {forbidden}"
        )

if '"enum": allowed_scopes' not in resolver:
    errors.append("semantic capability resolver is not enum-constrained to live scopes")
if "CapabilityProfile(" in resolver:
    errors.append("semantic resolver grants capabilities instead of resolving them")
if "_provision_managed_job_authority" not in autonomy:
    errors.append("resolved scopes do not flow through existing managed authority provisioning")
if "_create_policy" not in autonomy:
    errors.append("AI approval boundaries do not flow through existing governance services")
if "save_trigger_config" not in autonomy:
    errors.append("AI schedules do not flow through existing scheduler service")
if 'role="vision"' not in attachments:
    errors.append("image analysis bypasses generic vision model role")
if 'role="planner"' not in read("backend/app/runtime/planner/adaptive.py"):
    errors.append("Runtime planner is not using the provider-neutral planner role")
if "ActionGateway(" not in managed:
    errors.append("Managed Runtime no longer preserves F29 Action Gateway authority")

if errors:
    for error in errors:
        print(f"Worker autonomy authority failure: {error}", file=sys.stderr)
    raise SystemExit(1)

print("F31 Worker autonomy authority boundary verified.")
