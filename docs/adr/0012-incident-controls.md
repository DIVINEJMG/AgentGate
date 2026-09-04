# ADR 0012 — Incident controls and kill-switch semantics

## Context
AgentGate needs emergency execution stops that do not destroy agent identities, integration credentials, provider configuration, or incident evidence.

## Decision
Execution-control state is separate from lifecycle/configuration state. Organization, agent and integration targets can each be `active` or `suspended`. The Action Gateway checks all applicable control levels before provider execution, and approval-time resume repeats the same checks.

Incidents use `open → acknowledged → resolved`. Resolving an incident does not restore execution. Recovery is a separate human action with a reason, actor and timestamp.

The organization emergency stop is intentionally independent of individual target state: restoring the organization does not reactivate an agent/integration that is still separately suspended.

## Safety semantics
- A suspended control fails closed before provider execution.
- Emergency suspension is allowed even when audit emission is degraded; stopping execution has priority.
- Control changes and incident lifecycle changes emit append-only audit events on a best-effort management boundary.
- Agent lifecycle suspension/disable remains an additional independent execution guardrail.
- Provider writes remain disabled under the existing transactional/idempotency limitations.

## Storage limitation
AppDeploy KV does not provide transactional compare-and-set. Per-target control storage is bounded and a control-history overflow fails closed rather than guessing an active state.
