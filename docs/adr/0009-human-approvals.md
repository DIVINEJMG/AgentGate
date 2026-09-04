# ADR 0009 — Human Approval Lifecycle

## Context
Foundation 7 can hold `REQUIRE_APPROVAL` actions but cannot resume them. Foundation 8 needs an explicit human decision without turning approval into stale or transferable authority.

## Decision
Each held action receives one tenant-scoped pending approval record containing the original action and policy snapshot. Only humans with `approvals.review` may decide it. The first persisted decision is immutable. Reject blocks the held action. Approve records the human decision first, then revalidates agent lifecycle, initiating credential fingerprint, integration state, declared scope, provider operation and current policy before the exact held action can resume.

Approval notifications are best-effort and do not affect authorization. The current notification target is the agent owner when that user still belongs to the tenant with `approvals.review`; future membership foundations may expand recipient selection.

## Consequences
A human approval cannot revive a suspended agent, rotated credential, disconnected integration, removed capability or newly denied action. The approval record preserves the original policy while execution stores the reauthorization result.

AppDeploy's current key-value persistence does not expose transactional compare-and-set semantics. Foundation 8 therefore does not claim strongly serialized simultaneous decisions, and provider writes remain disabled. A future write-capable gateway must introduce an atomic decision/idempotency lock before irreversible side effects are enabled.
