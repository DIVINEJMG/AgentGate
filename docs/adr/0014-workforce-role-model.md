# ADR 0014 — Workforce and Role Model

## Status
Accepted for Foundation 13.

## Context
AgentGate F0–F12 established the security control plane. The Workforce Runtime architecture adds a business-facing managed Worker abstraction while preserving Agent Identity as the security-facing identity.

## Decision
- A Worker belongs to exactly one organization and binds immutably to one existing Agent Identity.
- One non-archived Worker may bind to an Agent Identity at a time.
- Worker status is `draft | active | paused | suspended | archived`; archived is terminal.
- Roles are reusable tenant-scoped business definitions containing purpose, default instructions, default responsibilities and recommended capability scopes.
- Recommended capability scopes are advisory only and never modify Agent capability declarations or authorization policy.
- Supervisors are human organization members and are validated server-side.
- Working hours are structured per weekday and use an IANA timezone.
- Worker lifecycle does not mutate Agent Identity lifecycle or Incident Controls. Those remain separate security boundaries.
- Foundation 13 introduces no scheduler, job queue, planner, LLM loop or autonomous provider execution.

## Consequences
The product can present an understandable AI workforce directory without weakening the existing control plane. Future Jobs and Runtime foundations can reference Worker IDs while all external execution continues through Agent Identity and the Action Gateway.
