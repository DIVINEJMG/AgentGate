# AgentGate

**An SOT Product**

AgentGate is the control layer for supervising AI workers inside small and growing organizations. It is built as a security-first modular monolith with a stable canonical core, replaceable provider edges, coexistence-ready API versions, and deterministic authorization as the final authority.

## Current checkpoint

- Foundation 0 — Repository & engineering standards: complete
- Foundation 1 — Application bootstrap: complete
- Foundation 2 — Identity & organizations: complete
- Foundation 3 — Agent identity: complete
- Foundation 4 — Integration framework: complete
- Foundation 5 — Capability model: complete
- Foundation 6 — Policy engine: complete
- Foundation 7 — Action gateway: complete
- Foundation 8 — Human approvals: implemented
- Foundation 9 — Audit & observability: next

## Foundation 6

The Policy Engine evaluates canonical Agent + Resource + Action + Scope + Risk context and returns exactly one outcome: `ALLOW`, `DENY`, or `REQUIRE_APPROVAL`.

Evaluation is fail-closed. Preconditions deny inactive agents, non-live resources, unavailable scopes and undeclared capabilities before policy matching. If no enabled policy matches, the result is `DENY`. Highest priority wins; ties resolve as `DENY > REQUIRE_APPROVAL > ALLOW`.

Policies are revisioned and may be enabled or disabled without being hard-deleted.

## Foundation 7

The Action Gateway is now the only agent-triggered provider execution boundary. It authenticates the agent's non-human credential, establishes idempotency and correlation state, resolves the canonical capability, evaluates policy, and maps the result to `executed`, `blocked`, or `held`.

Only `ALLOW` can invoke an integration adapter. `DENY` never reaches the provider. `REQUIRE_APPROVAL` creates a held action bound to a human approval record. Foundation 7 deliberately enables only read-only GitHub metadata, issue and pull-request operations; provider writes remain unavailable.

## Foundation 8

Human Approvals turns held actions into an explicit reviewer workflow. Roles with `approvals.review` can inspect pending requests and record an immutable approve or reject decision through API v1 or v2 and the responsive Approvals control-plane surface.

Approve does not blindly execute stale authority: AgentGate revalidates the original agent credential fingerprint, agent lifecycle, integration, declared scope, provider operation and current policy before resuming the exact held action. Reject permanently blocks it. Approval requests may send a best-effort push alert to an eligible agent owner who has enabled notifications.

AppDeploy's current key-value store does not provide a transactional compare-and-set primitive, so provider writes remain disabled and concurrent approval serialization is not claimed as exactly-once.

## Architecture rules

1. Stable canonical domain; replaceable adapters.
2. Public API versions coexist instead of overwriting each other.
3. Security is architecture, not a pre-launch phase.
4. Dangerous actions must be attributable and explainable.
5. AppDeploy is the deployment environment, not the domain boundary.
