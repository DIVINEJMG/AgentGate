# F27 QStash

QStash is the scheduling and queued-delivery authority.

Scheduled messages target signed internal FastAPI endpoints. /internal/v1/runtime/dispatch validates the QStash signature and canonical dispatch payload before creating a PostgreSQL WorkItem.

Idempotency is enforced by (organization_id, idempotency_key). F27.21 live staging verification delivered a scheduled message and a deliberate duplicate while producing exactly one WorkItem.

QStash never bypasses the Action Gateway and never directly performs provider side effects.

## Free-plan recovery cadence

Normal work is event-driven: creating, continuing, or retrying a WorkItem publishes its runtime execution immediately. Recovery schedules are durability backstops only and must not be the primary execution path.

The staging recovery cadence is intentionally conservative:

- Runtime recovery sweep: every 10 minutes (`*/10 * * * *`).
- Transactional outbox recovery drain: every 15 minutes (`*/15 * * * *`).
- Heartbeat: every hour (`0 * * * *`).

This baseline consumes about 264 scheduled QStash messages per day before real workload, leaving most of the free-plan daily allowance for actual runtime executions and continuations. Do not restore the old every-minute runtime sweep or five-minute outbox drain on the free plan.

If QStash rejects a publish with HTTP 429, Aduoryn records ephemeral dispatch health in Redis. The Work Queue surfaces quota/rate-limit state while canonical WorkItem state remains safely queued in PostgreSQL. A later successful QStash publish clears the dispatch warning.
