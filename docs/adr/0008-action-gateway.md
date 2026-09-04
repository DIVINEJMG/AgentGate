# ADR 0008 — Action Gateway is the only provider execution boundary

## Context
AgentGate now has agent identity, integrations, capabilities and deterministic policies. Provider calls must not bypass those controls.

## Decision
All agent-triggered provider execution enters through one canonical Action Gateway. The gateway authenticates the non-human credential, establishes idempotency and correlation state, resolves a live integration capability, evaluates policy, and then maps the result to `executed`, `blocked`, or `held`.

`ALLOW` is the only decision that may call an integration adapter. `DENY` never reaches a provider. `REQUIRE_APPROVAL` is persisted as held and remains non-executable until the Human Approvals foundation adds a valid decision lifecycle.

Foundation 7 enables only read-only GitHub operations. Write/destructive operations remain unsupported. Idempotency state is established before provider execution; the current AppDeploy key-value store does not provide a unique transactional constraint, so side-effecting provider operations will remain disabled until AgentGate has an atomic idempotency mechanism suitable for writes.

## Consequences
The dashboard, future SDKs, MCP gateways and direct agent clients share one enforcement path. Policy changes do not require gateway rewrites, provider changes remain inside adapters, and later audit/risk modules can observe the action record without owning execution.
