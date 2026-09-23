from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = (ROOT / "backend" / "tests" / "test_security_invariants.py").read_text(encoding="utf-8")
EXTRA = (ROOT / "backend" / "tests" / "test_f28_11_20.py").read_text(encoding="utf-8")

required_markers = {
    "Human session does not authenticate as Agent": "test_human_and_agent_auth_schemes_are_disjoint",
    "Worker instructions never grant authority": "test_worker_instructions_and_memory_cannot_grant_authority",
    "Memory never grants authority": "test_worker_instructions_and_memory_cannot_grant_authority",
    "DENY outranks approval/allow": "test_deny_outranks_require_approval_and_allow",
    "Cross-tenant references fail": "test_cross_tenant_reference_fails",
    "Side effects require stable idempotency": "test_side_effect_requires_stable_idempotency",
    "Agent cannot approve own Action": "test_agent_cannot_approve_action",
    "Incident kill switch overrides execution": "test_incident_kill_switch_overrides_execution",
    "Resumed work revalidates security": "test_resumed_work_revalidates_security",
    "Job requirement does not independently authorize execution": "test_job_requirement_does_not_grant_capability",
    "Provider credential is a reference": "test_provider_credentials_require_secret_references",
}

combined = TESTS + "\n" + EXTRA
missing = [name for name, marker in required_markers.items() if marker not in combined]
if missing:
    raise SystemExit("Missing F28 security invariants: " + ", ".join(missing))

print("F28 permanent security invariant suite verified.")
