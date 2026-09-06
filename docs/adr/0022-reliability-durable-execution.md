# ADR 0022: Reliability and durable execution

Status: Accepted

## Decision

Foundation 21 strengthens the existing AppDeploy-backed runtime. It does not introduce PostgreSQL or another persistence service.

- Work Items remain the durable source of execution state and the runtime dispatch table remains the bounded scheduler index.
- A claim receives a random fencing token and monotonically increasing lease generation. Every running step heartbeats before and after execution and records a checkpoint.
- Expired claims are recovered with bounded exponential delay. After two recoveries, the Work Item moves to `dead_letter` and emits a critical audit event.
- Runtime action idempotency is stable across retries because it is derived from the Work Item and step index, not the Run attempt.
- One scheduled tick consumes at most one AI run. This is the initial global concurrency policy and keeps provider pressure bounded.
- Retry delay uses bounded exponential backoff, from five minutes up to one hour.
- Scheduler and runtime scans remain cursor-checkpointed and bounded.

## Guarantees and limits

AppDeploy database exposes durable CRUD but no transaction or compare-and-swap primitive. Claims therefore use write-then-read verification and lease fencing. This prevents a stale worker from renewing a superseded lease and makes recovery deterministic, but it is not represented as serializable exactly-once execution. External writes are protected by the Action Gateway idempotency record and stable per-step key.

## Failure behavior

A runtime restart leaves the Work Item and dispatch record intact. When the lease expires, the scheduler requeues it with a recovery checkpoint. Repeated lease expiry moves it to the dead-letter state for human inspection instead of retrying forever.

