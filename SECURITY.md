# AgentGate Security Policy

AgentGate is security-sensitive infrastructure. Security controls are architectural requirements.

## Baseline principles

- Fail closed for sensitive authorization decisions.
- Never commit secrets or plaintext credentials.
- Keep human identity and agent identity as separate trust models.
- Enforce tenant boundaries at every data-access boundary once persistence is introduced.
- Record correlation identifiers for executable action chains when the audit foundation is introduced.
- Prefer deterministic policy decisions; AI may add context but does not override authorization.

## Current scope

Foundation 3 introduces agent credentials, but plaintext credentials are never persisted. A secret is generated at issuance/rotation, returned once to the authenticated human manager, and only its SHA-256 digest plus fingerprint and lifecycle metadata are stored. Human sessions and agent credentials remain separate trust domains. External integration secrets and the execution gateway remain deferred to later dedicated security foundations.
