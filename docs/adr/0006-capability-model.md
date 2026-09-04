# ADR 0006 — Canonical resources, actions and scopes

## Context
Provider adapters expose different operation names and resource models. AgentGate needs a stable vocabulary for policy evaluation without coupling policies to GitHub-specific implementation details or treating connectivity as authorization.

## Decision
Every active integration projects one or more canonical resources. Provider adapters declare structured capability descriptors containing a canonical action, target, provider operation, stable scope, description and risk class.

For the first GitHub adapter, a connected repository projects a `repository` resource with read capabilities for metadata, issues and pull requests. Stable scopes are namespaced by provider, for example `github.repository.issues.read`.

Agents may persist a capability declaration containing scopes they expect to request. Declarations are validated against the tenant's current live capability catalog and are explicitly marked authorization `unresolved`. They do not grant provider access.

Disconnected integrations are excluded from the live capability catalog. Previously declared scopes that lose their backing resource are surfaced as stale rather than silently treated as active.

## Alternatives
- Treat integration `supportedOperations` as permissions.
- Put GitHub operation strings directly inside future policy rules.
- Grant agents scopes immediately when they declare them.
- Delete declarations automatically when an integration disconnects.

## Consequences
Foundation 6 can evaluate one stable resource/action/scope model regardless of provider. Future adapters add capability descriptors without rewriting the policy domain. Capability declarations remain useful intent metadata while fail-closed authorization is preserved.
