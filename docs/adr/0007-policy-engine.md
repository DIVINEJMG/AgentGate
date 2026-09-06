# ADR 0007 — Deterministic policy engine

## Context
Audoryn now knows human identities, non-human agents, provider connections, canonical resources, actions and scopes. It needs an authorization decision layer before any action gateway can safely execute provider operations.

## Decision
Foundation 6 introduces a provider-neutral policy record with an effect (`allow`, `deny`, or `require_approval`), integer priority, enabled/disabled lifecycle state, revision number, and selectors for agents, resources, actions, scopes and capability risk.

Evaluation is deterministic:

1. Preconditions are evaluated first. An inactive agent, non-live resource, unavailable scope, or undeclared scope returns `DENY`.
2. Only enabled policies that match the canonical request context are considered.
3. If no policy matches, the result is `DENY`.
4. The highest policy priority wins.
5. If multiple policies share the highest priority, safety precedence is `DENY > REQUIRE_APPROVAL > ALLOW`.

Policy evaluation is side-effect free. Foundation 6 provides dry-run evaluation only; it cannot invoke an integration adapter or provider. Foundation 7 will place the Action Gateway in front of provider execution and consume these decisions.

## Consequences
- Default deny is explicit and testable.
- Specific exceptions are expressed by assigning them a higher priority than broader rules.
- A policy record can be disabled without deleting its history; every modification increments its revision.
- The policy engine remains independent of GitHub or any future provider.
- Approval decisions can be represented before the approval queue itself exists.

