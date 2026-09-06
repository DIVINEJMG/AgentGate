# ADR 0004 — Agent identities are independent security principals

## Context
Audoryn exists to supervise non-human workers. Reusing a human owner session or storing recoverable agent secrets would collapse the trust boundary the product is intended to enforce.

## Decision
Every registered AI worker receives an organization-scoped agent identity with a human owner, explicit lifecycle state, and an independently issued credential. Plaintext credentials are returned only at issuance or rotation; persistent storage contains only a SHA-256 digest, fingerprint and non-secret metadata. Human AppDeploy sessions are never accepted as agent credentials.

Lifecycle states are `active`, `suspended`, and `disabled`. Suspension is reversible. Disabled identities are terminal in Foundation 3. Credential revocation suspends the agent, and credential rotation is required before a revoked identity can become active again.

## Alternatives
Reuse human OAuth tokens; store recoverable plaintext agent keys; model agents as ordinary users.

## Consequences
Agent authentication can later be placed in front of the Action Gateway without sharing human trust. Rotation and revocation are possible without credential recovery. Credential verification must fail closed and, when introduced, must validate both the authoritative credential record and agent lifecycle state.

