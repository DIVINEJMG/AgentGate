from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


models = read("backend/app/infrastructure/database/models.py")
migration = read("backend/migrations/versions/0007_f31_conversation.py")
intent = read("backend/app/domain/conversation/intent.py")
context = read("backend/app/application/services/conversation_context.py")
interpreter = read("backend/app/application/services/intent_interpreter.py")
compiler = read("backend/app/application/services/conversation_commands.py")
service = read("backend/app/application/services/conversations.py")
routes = read("backend/app/api/conversation_routes.py")
realtime = read("backend/app/realtime/contracts.py")
v1 = read("backend/app/api/v1/router.py")
v2 = read("backend/app/api/v2/router.py")
tests = read("backend/tests/test_f31_11_20.py")

for token in (
    "class ConversationThread",
    "class ConversationMessage",
    "class ConversationCommand",
    "class WorkerDirective",
    "artifact_references",
    "command_references",
    "result_references",
):
    if token not in models:
        errors.append(f"F31.11 persistence is missing: {token}")

for table in (
    "conversation_threads",
    "conversation_messages",
    "conversation_commands",
    "worker_directives",
):
    if table not in migration:
        errors.append(f"F31.11 migration is missing table: {table}")

for token in (
    "worker.create",
    "worker.status",
    "worker.pause",
    "worker.resume",
    "job.stop",
    "job.retry",
    "schedule.update",
    "policy.add",
    "instruction.add",
    "result.query",
    "failure.explain",
    "work.execute_now",
    "integration.require",
    "attachment.analyze",
    "conversation.answer",
):
    if f'"{token}"' not in intent:
        errors.append(f"F31.13 intent family is missing: {token}")

for token in (
    "standingDirectives",
    "activeSchedules",
    "connectedIntegrations",
    "allowedCapabilities",
    "policiesAndApprovalBoundaries",
    "currentWork",
    "recentRuns",
    "recentResults",
    "relevantMemories",
    "relevantAttachments",
):
    if token not in context:
        errors.append(f"F31.12 authoritative context is missing: {token}")

for forbidden in (
    "IntegrationCredential",
    "ciphertext",
    "secret_hash",
    "storage_key",
):
    if forbidden in context:
        errors.append(f"F31.12 context exposes forbidden secret/storage data: {forbidden}")

if 'role="intent"' not in interpreter or "WorkerCommandIntent.model_validate" not in interpreter:
    errors.append("F31.14 interpreter is not schema-validated through the intent role")

for token in (
    "CrossTenantReferenceError",
    "waiting_confirmation",
    "clarification_required",
    "require_permission",
    "create_job(",
    "update_job(",
    "save_trigger_config(",
    "cancel_item(",
    "_create_policy(",
    "_update_policy(",
    "policy_status_v1(",
    "set_worker_status(",
    "retry_item_v1(",
    "queue_job(",
):
    if token not in compiler:
        errors.append(f"F31.15/F31.16 compiler is missing: {token}")

for forbidden in (
    "UniversalProviderExecutor",
    "ActionGateway",
    "NvidiaNimProvider",
    "Gmail",
    "GitHubProvider",
    "SlackProvider",
):
    if forbidden in compiler:
        errors.append(f"conversation compiler crosses side-effect/provider boundary: {forbidden}")

for path in (
    '"/organizations/{organization_id}/ask"',
    '"/organizations/{organization_id}/workforce/workers/{worker_id}/ask"',
    '"/organizations/{organization_id}/conversations/{thread_id}/messages"',
):
    if path not in routes:
        errors.append(f"F31.17/F31.18 conversation surface missing: {path}")

if "conversation_router" not in v1 or "conversation_router" not in v2:
    errors.append("conversation routes are not exposed through both API versions")

for event in (
    "conversation.message.created",
    "conversation.response.created",
    "conversation.command.accepted",
    "conversation.command.completed",
    "conversation.approval_required",
    "conversation.integration_required",
    "worker.status.changed",
):
    if f'"{event}"' not in realtime:
        errors.append(f"F31.19 realtime event missing: {event}")

for scenario in (
    "test_intent_interpreter_uses_intent_role_and_authoritative_context",
    "test_org_and_worker_conversation_surfaces_are_registered_in_v1_and_v2",
    "test_submit_approval_directive_compiles_to_existing_browser_submit_scope",
    "test_provider_outage_message_never_claims_business_task_failed",
    "test_destructive_and_cross_tenant_safety_paths_exist",
):
    if scenario not in tests:
        errors.append(f"F31.20 acceptance test missing: {scenario}")

if "ai_gateway_from_settings" not in service:
    errors.append("conversation service is not wired through provider-neutral AIGateway")

if errors:
    for error in errors:
        print(f"F31.11-F31.20 verification failure: {error}", file=sys.stderr)
    raise SystemExit(1)

print("F31.11-F31.20 conversational command layer verified.")
