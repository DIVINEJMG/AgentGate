# Shared integration foundation

## Scope and default

`INTEGRATION_FOUNDATION_ENABLED=false` preserves the existing execution path.
The new path adds private account ownership, explicit sharing, exact resource
bindings, asynchronous chat tasks, durable notification identities and a
verified event inbox. Browser behavior and existing GitHub operations are not
expanded by this change. No new provider SDK is required.

## Authority and execution

Connections and resources are separate records. The original integration ID is
retained as the alias of its original resource. Only an owner or explicitly
shared human can use a connection; organizational management rights are not
personal-account execution rights. Worker sharing cannot replace the human's
access. A revoked worker share also blocks that worker.

The shared compiler resolves instructions against accessible resource identities
and declared provider capabilities. Missing or ambiguous requirements remain
saved for clarification or connection setup. Provider text is evidence, never
authority. Worker creation produces standing revision-bound grants. A chat
`integration.execute` command produces a manual one-shot job with a grant bound
to its exact work item and initiating human. It cannot be reused by queueing the
job again. `work.execute_now` remains the existing-job command.

Worker creation makes one explicit AI provider selection per job, using the
human request, proposed job context, enabled tool guide and accessible resources.
Repository coordinates and URLs identify resources; they do not select a
browser. Native, browser, research, mixed and internal-only work are supported.
The selected providers replace conflicting draft suggestions. Browser origins
come only from AI-selected website targets validated against the human request;
integration URLs are not added to browser authority. Unavailable integrations
remain setup blockers instead of silently falling back to browsing.

Each action rechecks tenant, human membership, account access, worker/task grant,
resource capability, destination constraints, consent and policy. Approval
fingerprints include payload, resource and authority version. The planner chooses
business actions from observed provider results; the foundation supplies no
provider-specific business workflow.

### Capability readiness and recovery

Provider contracts describe optional coding runtimes separately from connectable
accounts. E2B belongs to GitHub's workspace capabilities; it must never appear as
a missing account connection. Task compilation records runtime requirements and
rejects a coding plan that omits its workspace tools. A disabled runtime produces
a setup reason while repository-only work remains available.

Resource catalogs refresh runtime availability against the enabled provider
manifest and recorded consent. This changes tool availability, never existing
task authority. Chat requests resolve their own explicitly named repositories
and retain request-only grants instead of inheriting the standing job's resource.

Readiness recovery compares complete bindings, scopes, constraints and authority
versions even when grants already exist. Repairs create a new immutable revision;
completed work retains its original revision and grants. Existing paths and
branches remain restricted. Resource changes or additional business capabilities
require review. Planner catalogs recheck the same task authorization as execution
and expose capability exclusion reasons to the planner.
Resource discovery queues saved chat preparation and current waiting worker
revisions through the signed integration processor. Worker recovery locks the
job and checks its current status, so duplicate delivery cannot restart an
already activated job.

Redis coordinates renewal, concurrency, rate budgets and resource mutations.
PostgreSQL owns task state, immutable revisions, dispatch certainty, grants,
messages and outbox events. Before a write reaches the provider, its dispatch
checkpoint is committed. An interrupted or timed-out write is reconciled before
replay. Without conclusive reconciliation it pauses as uncertain. Reads retain
bounded retry behavior. Completed checkpoints are not restarted.

Credential bundles remain encrypted and outside planner/message context. Static
tokens remain compatible. Expiring credentials use optional adapter renewal
hooks, coordinated by a Redis lease and PostgreSQL row lock. Rejected renewal
requires reconnecting; a changed account identity is rejected. Authentication
hooks are contracts, not implemented OAuth flows. Existing adapters do not claim
OAuth, revocation, event intake or reconciliation they cannot perform.

## Messages and events

Acknowledgment, progress, waits, approval, partial outcomes and completion use the
work item's originating conversation. Scheduled and event work needs an explicit
results conversation. Database uniqueness deduplicates notifications; message
and outbox insertion share the state transaction. Grounded outage messages do not
require an AI call. External references are structured adapter evidence, not
URLs invented from generated prose.

Event intake requires implemented authenticity/normalization hooks and a trusted
account identity supplied by authentication. Deliveries are bounded and
deduplicated before asynchronous processing. Only configured subscriptions with
standing authority can create work. Self-generated events identified by the
adapter are suppressed. Each provider must implement trustworthy self-event
classification and signature verification before enabling intake.

## Interfaces and configuration

Compatible foundation routes under both `/api/v1` and `/api/v2` expose readiness,
connections, ownership review, sharing, resources/discovery, task preparation,
reconnect/resume, authentication orchestration and event subscriptions. Incoming
events use `/integration-events/{provider_id}`. Unsupported hooks return an
unavailable response. Signed QStash processing uses
`/internal/v1/integrations/process`, derived from the configured runtime callback
origin. Pending preparation stays durable when QStash is unavailable.

Defaults: organization concurrency 4, connection concurrency 2; per-minute
budgets 60 and 20 respectively. Configure these with the `INTEGRATION_*` values
in `.env.example`. The authentication callback origin must be configured HTTPS
before a provider-specific authentication implementation can be used.

## Local adoption gate

1. Keep the flag disabled and preserve a database backup.
2. Upgrade migration `0009_integration_foundation` on an isolated test database.
3. Review connection ownership and legacy scope-only authority through the review
   API. Backfill trusts recorded creators only when they are current members;
   everything else requires administrator review. No broad resource grants are
   backfilled and existing ciphertext is unchanged.
4. Review exact resource bindings, results conversations and provider consent.
5. Enable only locally, then exercise a shared-account chat task, interruption,
   reconnect and verified completion in an explicitly authorized repository.
6. Verify actual upgrade/downgrade compatibility before any production adoption.

Current verification includes deterministic adapters, backend tests, lint,
scoped type checks, frontend tests/type checks/build and offline migration SQL.
Offline SQL generation does not prove a database upgrade. Live GitHub execution,
actual isolated database migration and OAuth readiness are deferred at the user's
request until GitHub implementation. The foundation must remain disabled until
those adoption checks are complete. No production migration, push or deployment
is part of this implementation.
