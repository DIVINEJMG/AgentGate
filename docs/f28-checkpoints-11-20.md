# F28.11–F28.20 Completion Record

This checkpoint continues the Python migration after F28.0–F28.10 and establishes the
domain-migration, background-execution, secret, artifact, and observability boundaries.

## F28.11 — Integration contract redesign

Canonical provider manifests now advertise:

- provider
- resources
- capabilities
- credential strategy
- operations

Each operation defines resource, action, scope, risk, input schema, output schema,
side-effect class, and approval recommendation.

## F28.12 — Domain-by-domain migration

The migration order is encoded in `app/application/migration_order.py` and rejects
authoritative domains whose prerequisite has not migrated. Runtime remains intentionally
late, after governance and queue infrastructure.

## F28.13 — Behavioral parity harness

The parity suite keeps TypeScript reference paths and normalized expected outcomes.
Python behavior is compared against the preserved TypeScript reference contract rather
than silently redefining behavior during migration.

## F28.14 — Permanent security invariants

CI now requires automated coverage for identity separation, capability/default-deny,
policy precedence, cross-tenant failure, stable idempotency, human-only approval,
incident kill switch, resumed-work revalidation, job-requirement non-authority, and
secret-reference handling.

## F28.15 — Transaction + outbox

`TransactionalOutbox` writes events inside the caller's PostgreSQL transaction.
`ActionApprovalTransaction` atomically records Action, optional Approval, AuditEvent,
and OutboxEvent. The outbox worker publishes after commit using the replaceable queue
boundary.

## F28.16 — Runtime worker boundary

The repository exposes independent process entry points:

- `audoryn-worker`
- `audoryn-scheduler`
- `audoryn-outbox`

The FastAPI process does not import or run worker/scheduler loops.

## F28.17 — Background execution abstraction

Application code targets `QueueBroker`; `ProviderQueueBroker` adapts that contract
to the configured queue provider. QStash is infrastructure, not a domain dependency.

## F28.18 — Secrets architecture

Provider credentials are represented as `secret://...` references. `SecretVault`
is the domain boundary and `SettingsSecretVault` is the current server-side adapter.
Secret values are never an API/frontend contract.

## F28.19 — Object storage

Artifacts continue through the provider-neutral `ObjectStorage` protocol with put,
get, delete, and signed URL operations. Upstash Blob is an adapter, not a domain type.

## F28.20 — Observability

HTTP requests receive a correlation ID and runtime work items propagate organization,
job, work item, and correlation context into structured JSON events. Provider-neutral
metric names are defined for run duration, action latency, policy denials, approval
wait, runtime retries, and provider failures.

## Exit gate

F28.11–F28.20 is complete only while CI passes:

- Ruff
- Pyright
- pytest
- security invariants
- parity tests
- API contracts
- F28.0–10 architecture gates
- F28.11–20 boundary verification
- frontend typecheck/build

This checkpoint does not enable autonomous runtime execution; the production runtime
feature flag remains fail-closed until a later explicit cutover.
