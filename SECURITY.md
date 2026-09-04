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
