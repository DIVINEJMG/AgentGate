# F27 Finalization

F27 is complete under the current temporary shared-environment operating model.

## Active architecture

GitHub
- source of truth for code, branches, PRs, CI and rollback history

Vercel
- audoryn frontend

Render
- audoryn-api-staging as the active API service

Neon
- audoryn-staging as canonical PostgreSQL

Upstash
- audoryn-redis-staging for coordination
- QStash EU for scheduling and queued delivery
- audoryn-artifacts-staging for private artifact storage

## Verified properties

- PostgreSQL is canonical.
- Redis is non-authoritative.
- Blob bytes are behind ObjectStorage.
- QStash delivery is signature verified.
- WorkItem creation is tenant-scoped and idempotent.
- QStash duplicate delivery does not create duplicate WorkItems.
- Runtime cutover stage is prepared.
- Autonomous execution remains disabled.
- Action Gateway remains the required external side-effect boundary.
- Legacy Render resources are retained for rollback.
- Existing AWB resources were not reused or modified.
- CI covers migration compatibility, provider boundaries, security, parity and secret leakage.

## Temporary production exception

The original F27 plan required separate staging and production infrastructure. The user explicitly chose to use the existing verified staging resources as production for now.

This is an operational exception, not an architectural collapse: provider interfaces, secret boundaries, rollback paths and canonical-state rules remain unchanged.

When separate production infrastructure is introduced later, it should be done as a new controlled migration rather than by renaming, resetting or deleting the current active resources.

## Unused production Neon projects

Two audoryn-production Neon projects were created during provisioning before the shared-environment decision was confirmed. They are not active and must remain untouched unless explicitly approved for later use or cleanup.
