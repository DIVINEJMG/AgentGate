# F27 Infrastructure Provider Cutover

F27 moves Aduoryn infrastructure edges to provider-independent production boundaries while preserving GitHub as the code source of truth.

Current target:
- Vercel: React/TypeScript frontend.
- Render: FastAPI API and later worker compute.
- Neon: canonical PostgreSQL.
- Upstash Redis: non-authoritative coordination.
- Upstash QStash/Workflow: scheduled and queued HTTP delivery.
- Upstash Blob: artifact bytes behind ObjectStorage.

Hard rule: create new resources, test, migrate, cut over, verify, then quarantine old resources. Existing non-Aduoryn and legacy resources are never overwritten or deleted by default.

Staging completed F27.3-F27.22. Production provisioning uses distinct audoryn-production resources and never renames staging into production.
