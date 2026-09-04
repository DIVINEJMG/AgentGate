# ADR 0005 — Provider adapters and integration credential vault

## Context
AgentGate must support many external systems without coupling the canonical domain to GitHub, Slack, Gmail, n8n, MCP, or future providers. Provider credentials are more sensitive than ordinary integration metadata and cannot be persisted as plaintext.

## Decision
Provider-specific behavior implements a narrow `IntegrationAdapter` contract. The canonical integration service owns tenant access, lifecycle state and public metadata. Provider adapters own validation and provider health checks.

GitHub is the first adapter. Repository coordinates are parsed as `owner/repository`; AgentGate hardcodes GitHub's API endpoint and does not fetch arbitrary user-supplied URLs.

Public repositories may connect without credentials. Optional private credentials are encrypted using AES-256-GCM with a backend-only master secret stored through AppDeploy secrets. Database records contain ciphertext, IV, authentication tag and non-sensitive fingerprints only.

A provider connection advertises supported operations but does not authorize any agent to perform them. Canonical resources/actions/scopes arrive in Foundation 5 and policy decisions in Foundation 6.

## Alternatives
- Provider-specific conditionals throughout the backend.
- Plaintext provider tokens in the database.
- App-level shared GitHub token for all tenants.
- Giving integrations implicit agent permissions.

## Consequences
New providers implement the adapter contract without rewriting AgentGate core. Private provider connections require the credential vault to be configured. Secret rotation will require a deliberate future key-rotation migration strategy.
