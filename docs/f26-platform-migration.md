# F26 Platform Independence & Python Backend Migration

## F26.0–F26.15
Platform extraction, Vercel frontend hosting boundary, Python/FastAPI foundation, Render data
resources, PostgreSQL canonical state, Redis coordination, provider-independent identity,
Human/Agent separation, model-provider abstraction, Action Gateway and integration manifests are
established.

## F26.16–F26.20

### F26.16 — Object storage boundary
Artifacts now depend on an `ObjectStorage` protocol with put/get/delete/signed-url operations.
The default provider fails closed. PostgreSQL stores artifact metadata/object references, not object
bytes. S3/R2/Tigris/MinIO can be introduced behind the boundary without changing domain code.

### F26.17 — Scheduler and worker processes
The scheduler reads canonical due work from PostgreSQL and publishes Redis wake notifications. The
worker claims canonical work with PostgreSQL `FOR UPDATE SKIP LOCKED`, heartbeats through Redis,
and checkpoints success/failure back to PostgreSQL. Runtime execution remains disabled by default
until the remaining runtime migration is complete.

### F26.18 — Separate API / worker / scheduler
FastAPI, `python -m app.runtime.worker`, and `python -m app.runtime.scheduler` are separate
processes from one codebase. Render Blueprint definitions describe all three; no HTTP request is
required to remain open for autonomous work.

### F26.19 — v1/v2 adapters
`/api/v1/*` and `/api/v2/*` remain supported. System status now demonstrates the required
pattern: both API versions serialize one canonical application query rather than branching inside
domain behavior.

### F26.20 — Frontend/API separation
The browser now requires only `VITE_API_BASE_URL`. Auth uses the same API origin and realtime
derives ws/wss from that origin. Database, Redis, object-storage and provider secrets remain backend
only.

Platform rule: GitHub is source of truth; Vercel hosts frontend; Render hosts backend/runtime; domain
code depends on no hosting-platform SDK.
