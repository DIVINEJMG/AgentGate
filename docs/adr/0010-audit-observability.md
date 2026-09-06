# ADR 0010 — Append-only audit and correlation tracing

## Context
Audoryn must reconstruct who or what initiated an action, the resource involved, the policy/approval path, and the final provider result. Foundation 7 already establishes correlation IDs, but no unified event ledger existed.

## Decision
Foundation 9 introduces a tenant-scoped append-only audit module. The application exposes read-only v1/v2 audit APIs and no update/delete audit endpoints. Events contain actor, resource, category, severity, outcome, timestamp and optional correlation ID. Reads scan one bounded page of at most 200 recent records, then apply supported filters without draining the table.

Agent-triggered provider execution must establish a required audit event before invoking the provider. Final action events are appended after state persistence. Approval requests and human decisions also emit required correlated events. Administrative agent/integration/policy events use best-effort emission because the current KV store cannot atomically commit domain state and audit records.

No synthetic backfill is generated for pre-Foundation-9 history.

## Consequences
Critical runtime execution fails closed when the pre-execution audit path is unavailable. Correlation traces can connect request, policy, approval and outcome. Administrative audit completeness is not transactionally guaranteed. The ledger is application-level append-only, not a database-level WORM system; stronger atomic/outbox and retention storage may replace this adapter later without changing the canonical event contract.

