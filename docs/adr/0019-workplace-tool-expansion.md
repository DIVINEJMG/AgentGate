# ADR 0019 — Workplace Tool Expansion

## Status
Accepted for Foundation 18.

## Context
Audoryn's Action Gateway and provider adapter boundary are provider-neutral, but GitHub is the only implemented external system. The workforce runtime needs useful workplace context without introducing direct provider calls, arbitrary outbound URLs, fake OAuth lifecycle claims or a provider-specific authorization path.

## Decision
- F18 adds Gmail, Google Drive, Slack and Google Calendar adapters behind the existing `IntegrationAdapter` contract.
- Each executable adapter uses only fixed official API origins. Provider configuration cannot supply an outbound URL.
- Gmail exposes mailbox profile and five recent-message metadata/snippets; Drive exposes account identity and recent file metadata; Slack exposes workspace identity and bounded channels; Calendar exposes primary-calendar identity and ten upcoming events.
- F18 provider operations are read-only. Dynamic write payloads, send/create actions and their approval snapshots are deliberately not smuggled into the existing action contract.
- Credential-backed providers require the existing encrypted integration vault. F18 accepts manually supplied provider access tokens, validates them once, encrypts them at rest and makes no claim of implementing provider OAuth refresh lifecycle.
- Token expiry or revoked scopes surface through the normal degraded health state. Reconnection/replacement is required until a later OAuth lifecycle foundation exists.
- Provider capabilities remain technical availability, not Agent authority. Every runtime operation still resolves through capability declaration, risk, deterministic policy, approvals when required, audit and incident controls before `executeOperation` runs.
- Generic REST / MCP is represented as a guarded adapter but cannot connect or execute. Arbitrary URLs remain disabled until Audoryn has explicit outbound-origin allowlisting and egress validation robust enough to address SSRF and DNS-rebinding risk.

## Consequences
Audoryn moves beyond GitHub with real workplace read surfaces while preserving one authorization path. Write-side workplace actions, token refresh/OAuth app management and generic outbound egress remain explicit future work rather than hidden security debt.

