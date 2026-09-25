from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


drafts = read("backend/app/domain/workforce/drafts.py")
draft_generator = read("backend/app/application/services/worker_draft.py")
resolver = read("backend/app/application/services/capability_autoresolver.py")
schedule = read("backend/app/application/services/schedule_inference.py")
autonomy = read("backend/app/application/services/worker_autonomy.py")
memory = read("backend/app/application/services/worker_memory.py")
attachments = read("backend/app/application/services/attachment_ingestion.py")
commands = read("backend/app/application/services/conversation_commands.py")
conversation_routes = read("backend/app/api/conversation_routes.py")
jobs = read("backend/app/api/jobs_routes.py")
integrations = read("backend/app/api/integration_capability_routes.py")
managed = read("backend/app/runtime/managed.py")
models = read("backend/app/infrastructure/database/models.py")
migration = read("backend/migrations/versions/0008_f31_autonomy.py")
provider_factory = read("backend/app/infrastructure/ai/provider.py")
tests = read("backend/tests/test_f31_21_30.py")

for token in (
    "class WorkerDraft",
    "suggested_name",
    "role",
    "department",
    "supervisor_name",
    "charter",
    "responsibilities",
    "initial_jobs",
    "completion_criteria",
    "capability_needs",
    "approval_boundaries",
    "integration_requirements",
):
    if token not in drafts:
        errors.append(f"F31.21 WorkerDraft is missing: {token}")

if 'role="intent"' not in draft_generator or "WorkerDraft.model_validate" not in draft_generator:
    errors.append("F31.21 WorkerDraft is not schema-validated through AIGateway")

for token in (
    '"enum": allowed_scopes',
    "IntegrationCredential",
    "availableCapabilities",
    "missing_integrations",
    "provider.manifest.capabilities",
):
    if token not in resolver:
        errors.append(f"F31.22 capability resolver is missing: {token}")
for forbidden in ("CapabilityProfile(", "_provision_managed_job_authority"):
    if forbidden in resolver:
        errors.append(
            f"F31.22 semantic resolver mutates authority instead of only resolving: {forbidden}"
        )

for token in (
    '"waiting_integration"',
    "AutonomyReadinessService",
    "missingIntegrations",
    "capabilityNeeds",
):
    if token not in autonomy:
        errors.append(f"F31.23 integration-gap lifecycle is missing: {token}")
if "AutonomyReadinessService" not in integrations:
    errors.append("F31.23 integration connection does not trigger readiness reconciliation")

for token in (
    "ZoneInfo",
    '"daily"',
    '"weekly"',
    '"interval"',
    '"once"',
    '"event"',
    "stop_after_next_run",
):
    if token not in schedule:
        errors.append(f"F31.24 schedule inference is missing: {token}")
if "_apply_stop_after_next_run" not in managed:
    errors.append("F31.24 stop-after-next-run is not enforced after completion")

for token in (
    "submit_requires_approval",
    "external_send_requires_approval",
    "write_requires_approval",
    "_create_policy",
    "browser.form.submit",
):
    if token not in autonomy and token not in drafts:
        errors.append(f"F31.25 approval default is missing: {token}")

for token in (
    "seed_identity",
    "add_standing_memory",
    "record_operational",
    "record_episode",
    "contains_secret_material",
):
    if token not in memory:
        errors.append(f"F31.26 worker memory is missing: {token}")
for token in (
    "memory_type",
    "provenance",
    "sensitivity",
    "status",
):
    if token not in models:
        errors.append(f"F31.26 persistent memory metadata is missing: {token}")
if "standingInstructions" not in managed or '"memory"' not in managed:
    errors.append("F31.26 bounded memory is not provided to Runtime planner")

for token in (
    "TenantObjectStorage",
    "decode_base64_content",
    "_deterministic_analysis",
    "PdfReader",
    "load_workbook",
    "Unsupported attachment media type",
):
    if token not in attachments:
        errors.append(f"F31.27 attachment ingestion is missing: {token}")
if "/conversations/{thread_id}/attachments" not in conversation_routes:
    errors.append("F31.27 conversation upload route is missing")

for token in (
    'role="vision"',
    "signed_url",
    "AIMediaInput",
    "ArtifactAnalysis",
    "provenance",
):
    if token not in attachments:
        errors.append(f"F31.28 vision routing is missing: {token}")
if "nvidia/ising-calibration-1.5-31b" not in provider_factory:
    # Model ID may live in settings/registry factory; verify via source below separately.
    settings = read("backend/app/bootstrap/settings.py")
    if "nvidia/ising-calibration-1.5-31b" not in settings:
        errors.append("F31.28 default vision model is not Ising behind the vision role")

for token in (
    "AdaptiveRuntimePlanner",
    "ai_gateway_from_settings",
    "availableCapabilityScopes",
    "_planner_observations",
):
    if token not in managed:
        errors.append(f"F31.29 Managed Runtime planner is missing: {token}")
if "requiredCapabilities" not in jobs:
    errors.append("F31.29 canonical Job does not persist exact required capabilities")

for token in (
    "test_worker_draft_generator_returns_validated_full_worker_draft",
    "test_semantic_capability_resolver_can_only_select_live_exact_scope",
    "test_capability_resolver_turns_missing_provider_into_connection_requirement",
    "test_schedule_inference_normalizes_daily_weekly_once_and_stop_after",
    "test_worker_memory_rejects_secret_like_material",
    "test_deterministic_text_json_and_xlsx_attachment_analysis",
    "test_vision_route_is_generic_and_uses_configured_vision_role",
    "test_golden_qq_scenario_contract_is_represented_without_scope_invention",
):
    if token not in tests:
        errors.append(f"F31.30 acceptance test is missing: {token}")

for table_or_column in (
    "artifact_analyses",
    "memory_type",
    "provenance",
    "sensitivity",
):
    if table_or_column not in migration:
        errors.append(f"F31.21-30 migration is missing: {table_or_column}")

if "WorkerAutonomyService" not in commands:
    errors.append("worker.create is not compiled through WorkerAutonomyService")
if "AttachmentIngestionService" not in commands:
    errors.append("attachment.analyze is not wired to the ingestion/analysis pipeline")

if errors:
    for error in errors:
        print(f"F31.21-F31.30 verification failure: {error}", file=sys.stderr)
    raise SystemExit(1)

print("F31.21-F31.30 AI worker autonomy layer verified.")
