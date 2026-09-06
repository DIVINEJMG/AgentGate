# AgentGate Security Policy

AgentGate is security-sensitive infrastructure. Security controls are architectural requirements.

## Baseline principles

- Fail closed for sensitive authorization decisions.
- Never commit secrets or plaintext credentials.
- Keep human identity and agent identity as separate trust models.
- Enforce tenant boundaries at every data-access boundary.
- Treat provider connectivity, technical capability, and authorization as three different concerns.
- Record correlation identifiers for executable action chains when the audit foundation is introduced.
- Prefer deterministic policy decisions; AI may add context but does not override authorization.

## Agent credentials

Plaintext agent credentials are never persisted. A secret is generated at issuance/rotation, returned once to the authenticated human manager, and only its SHA-256 digest plus fingerprint and lifecycle metadata are stored.

## Integration credentials

Provider credentials use a different boundary from agent authentication. Tenant-supplied provider tokens are encrypted with AES-256-GCM before database persistence. The encryption key is derived from `INTEGRATION_MASTER_KEY`, which exists only in AppDeploy backend secret storage and is never returned to the client. Public GitHub repository connections require no provider token. If the vault is not configured, private credential-backed connections fail closed.

## Capability boundary

A capability records what a connected provider can technically expose. An agent capability declaration records scopes an agent expects to request. Neither structure authorizes execution. Declared scopes must exist in the organization's current live capability catalog, disconnected integrations advertise no active capabilities, and cross-tenant capability declarations are rejected.

## Policy boundary

Policy evaluation is deterministic and fail-closed. Inactive agents, non-live resources, unavailable scopes, and undeclared scopes return `DENY` before policy matching. If no enabled policy matches, the result is `DENY`. Highest priority wins; ties resolve with safety precedence `DENY > REQUIRE_APPROVAL > ALLOW`.

Policies are revisioned and can be disabled without deleting their history.

## Action Gateway boundary

Foundation 7 places deterministic decisions in the runtime execution path. External agent routes require the agent's separate bearer credential; human AppDeploy sessions do not satisfy agent authentication. Every accepted request establishes an idempotency key hash and correlation ID before provider execution. `DENY` never calls an adapter, `REQUIRE_APPROVAL` is held without execution, and only `ALLOW` can invoke a provider operation.

The first executable provider surface is intentionally read-only GitHub metadata, issue and pull-request retrieval. Provider writes remain unavailable. Agent secrets are never persisted by the gateway or test harness, integration tokens remain decrypted only inside the backend credential boundary, and provider failures are recorded as failures rather than converted into successful authorization.

## Human approval boundary

Foundation 8 persists a pending approval before a `REQUIRE_APPROVAL` action becomes reviewable. Only a same-tenant human with `approvals.review` may decide it. Decisions are immutable once persisted: an approved record cannot later become rejected and a rejected record cannot later become approved.

Approval does not reuse stale authority. Before provider execution, AgentGate verifies that the initiating agent is still active, the same credential fingerprint is still current and valid, the integration remains connected, the exact provider operation still exists, the agent still declares the scope, and current policy does not resolve to `DENY`. Any failed revalidation blocks execution. Push notification delivery is best-effort and never changes authorization state.

Because the current AppDeploy key-value store exposes no transactional compare-and-set primitive, AgentGate does not claim strong serialization for simultaneous competing approval clicks. Provider execution remains read-only until a storage boundary suitable for atomic decision/idempotency locking is available.

## Audit boundary

Foundation 9 exposes no audit update or delete API. Events are appended to a tenant-scoped ledger and can be read only by humans with `audit.read`. Correlation IDs connect agent requests, authorization outcomes, approvals and provider results. New provider execution is denied when its required pre-execution audit event cannot be persisted.

Administrative domain mutations and audit events cannot be atomically committed together with the current AppDeploy KV store. Those management events are therefore best-effort and failures are surfaced to backend observability logs. AgentGate does not claim database-level WORM guarantees or transactional audit completeness until a storage boundary supporting atomic mutation/outbox semantics is available.

## Incident-control boundary

Foundation 11 separates emergency execution state from identity and integration configuration. Organization, agent and integration controls can suspend execution without deleting credentials or changing provider configuration. The Action Gateway checks all three applicable control levels before provider invocation and repeats them before a held action resumes after approval.

Incident resolution never restores execution automatically. Recovery is a separate explicit human operation with `incidents.manage`, a reason and audit attribution. Organization restoration does not override a still-suspended agent or integration. Existing agent lifecycle suspension/disable remains an independent guardrail.

Because AppDeploy KV has no transactional compare-and-set, concurrent management transitions cannot be claimed as strongly serialized. Per-target control reads are bounded; ambiguous overflow fails closed rather than assuming execution is active.

## Productization boundary

Foundation 12 keeps commercial state outside runtime authorization. Workspace settings are tenant-scoped; reads require organization membership and writes require `organizations.manage`. Usage analytics use bounded reads and expose truncation instead of claiming exact totals from incomplete windows.

The initial billing boundary is observe-only. No payment processor, checkout flow, billing secret or client-side payment credential is present. Plan state cannot authorize agent actions or override policy, risk, approval, audit or incident controls. Entitlement enforcement must remain disabled until a future billing adapter has verified server-side provider state.

## Workforce boundary

Foundation 13 separates the business-facing Worker from the security-facing Agent Identity. Every non-archived Worker binds to exactly one existing Agent Identity, and a live Agent Identity cannot back multiple managed Workers. The binding is immutable for the life of the Worker.

Roles, responsibilities, instructions, departments, supervisors and working hours are management context only. Role capability recommendations never grant capabilities, Worker instructions never override policy, and Worker lifecycle does not mutate Agent Identity or Incident Controls. Supervisors are validated as same-tenant human members. Foundation 13 performs no autonomous execution.

## Jobs and Work Queue boundary

Foundation 14 keeps work definition separate from authority and execution. Job capability requirements never grant scopes. Job activation/resume and manual queueing revalidate the Worker, bound Agent Identity and current active capability declarations before work can enter the queue.

`Run now` persists a tenant-scoped Work Item only. The Work Item stores an immutable Job revision snapshot and correlation ID, but Foundation 14 has no scheduler, planner, Run Engine, Step Executor, provider call or autonomous loop. Humans may cancel only queued items. Strong queue claiming, duplicate-run protection, leases and execution idempotency remain explicitly deferred to the durable-execution foundation rather than being falsely claimed on the current key-value store.

## Scheduler and trigger boundary

Foundation 15 treats automation as a queue producer, never an authorization shortcut. Scheduled, internal-event, API and dependency triggers all create the same tenant-scoped Work Items used by manual F14 queueing. Required capabilities, Worker/Agent lifecycle, credential state and organization/agent incident controls are revalidated before a Work Item is admitted.

The public runtime trigger endpoint authenticates with the bound Agent Identity's existing credential; AgentGate does not introduce a second plaintext trigger secret. Schedule slots and optional API idempotency keys receive dedupe keys, but the current key-value store has no transactional compare-and-set, so Foundation 15 does not claim strong exactly-once behavior under concurrent trigger races. The scheduler processes one bounded checkpointed registry page every five minutes and never drains a growing registry in one invocation.

Working-hours and missed-run policies affect when work enters the queue; they never bypass policy, approvals, risk or incident controls. No F15 path creates a Run or calls a provider.

## Managed Runtime boundary

Foundation 16 introduces AI planning and Run execution without creating a new authorization authority. The model receives no Agent credential, provider token or direct provider-execution tool. Planner action proposals are validated against the bound Agent's current live capability/resource catalog before they become durable steps.

External action steps invoke the same Action Gateway used by direct Agent requests. The Managed Runtime uses a trusted server-side Agent Identity principal validated against the current credential record; the gateway still enforces capability declaration, deterministic policy, behavior risk, approvals, audit and organization/agent/integration incident controls. Held actions pause the Run rather than allowing the model to continue around an approval requirement.

Work Item claims use random lease tokens and re-read verification, but the current KV store has no atomic compare-and-set primitive. AgentGate therefore does not claim strong exactly-once execution under a simultaneous claiming race. Action idempotency remains authoritative for provider requests; provider writes remain outside the current read-only adapter surface. Autonomous AI execution is intentionally hourly and limited to one AI Run per cron invocation, while the five-minute scheduler performs no AI work.

## Memory and artifact boundary

Foundation 17 treats memory as untrusted context, never as authorization. Managed Runtime may automatically persist only Run Memory. Worker Memory and Organization Knowledge require a same-tenant human with `memory.manage`; promotion from Run Memory to Worker Memory is an explicit human action.

Context assembly is bounded, excludes archived/expired records and stores the selected memory IDs on the Run for traceability. Memory content is never evaluated as capability state and cannot alter risk, deterministic policy, approvals or incident controls. Expiry means a record is no longer context-eligible; F17 does not claim physical retention deletion.

Successful Run artifacts are stored separately in AppDeploy Storage with tenant-scoped metadata. Access requires `artifacts.read` and is issued through short-lived signed URLs. Provider credentials and Agent credentials are never written into memory or artifacts by this foundation.

## Workplace tool expansion boundary

Foundation 18 adds Gmail, Google Drive, Slack and Google Calendar without adding a second execution path. Provider operations remain behind the existing capability catalog, risk engine, deterministic policy, approval, audit and incident-control chain before an adapter may execute.

All F18 executable providers use fixed official API origins. User configuration cannot supply arbitrary outbound URLs. Provider access tokens require the AES-256-GCM integration vault, are never returned after connection, and are decrypted only inside the backend provider boundary. F18 does not claim OAuth refresh-token lifecycle; expired or revoked tokens degrade the connection and require replacement/reconnection.

F18 workplace operations are intentionally read-only. Generic REST / MCP is fail-closed and cannot connect or execute until AgentGate has an explicit outbound-origin allowlist and egress controls strong enough to address SSRF and DNS-rebinding risk.

## Supervisor and escalation boundary

Foundation 19 treats escalation as human supervision work, not authorization. Final Job failures, policy intervention and elevated runtime risk can create a durable supervisor record, but that record cannot grant capabilities, lower risk, change policy outcomes or execute a provider action.

Supervisor interventions are monotonic restrictions or incident handoffs. Run cancellation persists a correlation-level cancellation marker and the Action Gateway checks it both before managed execution and before a held action resumes after approval. Worker pause changes only the Workforce lifecycle and does not mutate Agent Identity, provider credentials or incident controls. Incident creation delegates to the existing F11 incident domain.

Push notifications are best-effort and never become the source of truth. The tenant-scoped escalation inbox is authoritative. Foundation 19 does not claim the ability to undo a provider side effect that completed before a supervisor cancellation request, and does not claim strong exactly-once escalation dedupe until the durable execution work in F21.

## Workforce performance boundary

Foundation 20 is observational only. `performance.read` grants tenant-scoped access to bounded operational analytics but no mutation, execution, capability, policy, approval, incident or provider authority.

Performance metrics are derived from existing canonical records and never feed back into authorization. AgentGate does not create a composite employee quality, productivity or trust score. Provider reliability excludes policy blocks and approval holds from its failure denominator so security controls cannot make a tool appear unreliable. Bounded-source truncation is disclosed instead of hidden.

## Risk boundary

Foundation 10 keeps risk deterministic. Provider capability risk is the floor and behavior can only maintain or raise it. The current signals are burst requests, repeated blocked actions, repeated provider failures, approval pressure and bounded-history truncation. Each active signal raises risk one level, capped at `critical`; no AI model can authorize or lower risk.

Policy evaluation and runtime execution consume effective risk. Approval-time reauthorization recomputes risk instead of reusing the held snapshot. If risk assessment cannot be completed, the execution path fails closed. The behavior read is bounded to 100 action records; when that window is truncated, uncertainty conservatively raises risk rather than silently underestimating it.
