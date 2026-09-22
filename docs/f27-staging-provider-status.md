# F27 staging provider status

## Active staging infrastructure

### Neon
- Project: audoryn-staging
- Region: aws-eu-central-1
- Database: audoryn
- Application role: audoryn_app
- Migration role: audoryn_migrator
- Alembic version: 0003_f27_provider_metadata

### Upstash — current Aduoryn account
- Redis: audoryn-redis-staging
  - eu-central-1
  - TLS enabled
  - eviction disabled
  - direct SET/GET verification passed
- Blob: audoryn-artifacts-staging
  - private
  - signed PUT upload verification passed
- QStash: EU
  - server credentials configured in Render staging
  - signed one-off heartbeat delivery returned 200 OK
  - active hourly heartbeat schedule targets:
    https://audoryn-api-staging.onrender.com/internal/v1/runtime/heartbeat
- Workflow/DLQ:
  - QStash DLQ reachable and empty
  - Workflow DLQ reachable and empty

### Render
- Service: audoryn-api-staging
- Branch: main
- Region: Frankfurt
- Plan: free
- Current deploy: live
- DATABASE_URL: Neon staging
- REDIS_URL: current-account Upstash Redis
- QStash credentials: current-account EU QStash
- UPSTASH_BLOB_BUCKET: current-account private Blob bucket
- CUTOVER_STAGE: system
- SHADOW_MODE_ENABLED: true
- RUNTIME_EXECUTION_ENABLED: false

The older audoryn-api Render service remains untouched as fallback.

## QStash schedules

One earlier test schedule points at the old API health endpoint and is paused. It remains preserved
rather than deleted.

The active staging schedule targets the signed POST heartbeat endpoint on audoryn-api-staging.

Real job schedules will later be created dynamically from canonical job definitions and will target
the verified runtime dispatch endpoint rather than bypassing PostgreSQL or the Action Gateway.

## Blob credential boundary

The current Upstash connector intentionally does not expose private bucket tokens. The bucket is
created and actual storage writes are verified through provider-signed upload URLs.

The FastAPI ObjectStorage transport must remain fail-closed until the server-only
UPSTASH_BLOB_TOKEN is supplied securely to Render. This token must never be committed to GitHub,
stored in Vercel frontend variables, or exposed to the browser.

## Account rule

The currently connected Upstash account is the only active Upstash account for Aduoryn going
forward. Resources in any earlier Upstash account are historical only and must not be modified,
reused, deleted, or treated as active infrastructure.

## F27.21-F27.30 progress

### Scheduler cutover
- CUTOVER_STAGE moved to scheduler, then runtime after live validation.
- RUNTIME_EXECUTION_ENABLED remains false.
- QStash schedule scd_672GdnaxKhWnGzqor3za5AB6eDd2 delivered to /internal/v1/runtime/dispatch and was paused after verification.
- Duplicate delivery with the same organization/idempotency key produced exactly one WorkItem.

### Production provisioning
- Neon production project: audoryn-production (AWS eu-central-1).
- Production Neon schema is at Alembic 0004_f27_queue_failures.
- Upstash Redis production creation is currently blocked by the connected account free-plan one-database limit.
- Upstash Blob production creation is currently blocked by the connected account free-plan one-bucket limit.
- Production Render/QStash cutover remains gated until separate production Redis and Blob resources exist; staging resources will not be reused.

### Runtime safety
- Staging CUTOVER_STAGE=runtime.
- RUNTIME_EXECUTION_ENABLED=false.
- Legacy Render PostgreSQL/Redis remain preserved for rollback.
