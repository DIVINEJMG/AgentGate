# F27 Infrastructure Provider Cutover

F27 moves Aduoryn infrastructure edges to provider-independent production boundaries while preserving GitHub as the code source of truth.

Current active stack:
- Vercel: React/TypeScript frontend.
- Render: FastAPI API.
- Neon: canonical PostgreSQL.
- Upstash Redis: non-authoritative coordination.
- Upstash QStash/Workflow: scheduled and queued HTTP delivery.
- Upstash Blob: artifact bytes behind ObjectStorage.

Hard rule: create, test, migrate, cut over, verify, then quarantine old resources. Existing non-Aduoryn and legacy resources are never overwritten or deleted by default.

## Temporary shared-environment decision

For the current phase, the user explicitly approved the already-verified Aduoryn staging resources to serve as the operational production resources as well.

Active shared resources:
- Neon: audoryn-staging
- Upstash Redis: audoryn-redis-staging
- Upstash Blob: audoryn-artifacts-staging
- QStash EU schedules/delivery
- Render: audoryn-api-staging
- Vercel: audoryn

This is an intentional temporary exception to the original separate-production-resource plan. It does not permit reuse of AWB resources.

Two separately-created Neon projects named audoryn-production are not part of the active stack. They are reserved/unused and must not be written to, repurposed, reset, or deleted unless explicitly approved later.
