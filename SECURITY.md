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
