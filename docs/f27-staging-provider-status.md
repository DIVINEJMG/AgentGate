# F27 provider status

## Active shared staging/production infrastructure

### Neon
- Active project: audoryn-staging
- Region: aws-eu-central-1
- Database: audoryn
- Canonical PostgreSQL for the current operational environment
- Schema migration chain verified through 0004_f27_queue_failures

Two separately-created audoryn-production Neon projects exist but are not active. They are reserved/unused and must remain untouched unless explicitly approved later.

### Upstash
- Redis: audoryn-redis-staging
  - eu-central-1
  - TLS enabled
  - eviction disabled
  - live PING verification passed
- Blob: audoryn-artifacts-staging
  - private
  - live upload/download/signed URL/delete smoke verification passed
- QStash: EU
  - server credentials configured in Render
  - signed deliveries verified
  - active hourly heartbeat targets audoryn-api-staging
  - F27 scheduler verification dispatch produced exactly one WorkItem and duplicate delivery remained idempotent
- Workflow/DLQ boundaries are available for retry/failure handling

### Render
- Active service: audoryn-api-staging
- Branch: main
- Region: Frankfurt
- Current deploy: live
- DATABASE_URL: active Neon audoryn-staging
- REDIS_URL: audoryn-redis-staging
- QStash: current Aduoryn EU account
- Blob: audoryn-artifacts-staging
- CUTOVER_STAGE: runtime
- SHADOW_MODE_ENABLED: true
- RUNTIME_EXECUTION_ENABLED: false

The older audoryn-api Render service remains fallback-only.

### Vercel
- Project: audoryn
- Frontend is served by Vercel.
- Backend secrets remain server-side; frontend uses only its public API base configuration.

## F27.21-F27.30 verification

### F27.21 scheduler cutover
- Real QStash schedule delivered to /internal/v1/runtime/dispatch with HTTP 200.
- Exactly one PostgreSQL WorkItem was created.
- Re-delivery with the same tenant/idempotency key left the WorkItem count at one.
- The verification schedule was paused after the test.

### F27.22 runtime preparation
- CUTOVER_STAGE=runtime.
- RUNTIME_EXECUTION_ENABLED=false.

### F27.23-F27.25 operational production decision
- The user approved the already-tested staging resources to serve as production for now.
- Separate Upstash production resources are therefore not required for F27 completion.
- The active Render/Vercel/Neon/Redis/QStash/Blob path has been live-verified.

### F27.26 legacy quarantine
- Legacy Render PostgreSQL/Redis remain preserved for rollback.
- Older Render API is fallback-only.
- No old infrastructure was deleted.

### F27.27 rollback
- Rollback procedures are documented in docs/f27-rollback.md.

### F27.28 CI
- Ruff, Pyright, contract, parity, security, provider-boundary, idempotency and migration compatibility checks pass.

### F27.29 documentation
- F27 infrastructure, Neon, Redis, QStash, Blob, cutover and rollback documents are present.

### F27.30 Definition of Done
F27 is complete for the current shared-environment operating model:
- Neon canonical PostgreSQL: yes
- Upstash Redis coordination: yes
- Upstash Blob artifacts: yes
- QStash scheduling/queued delivery: yes
- Vercel frontend: yes
- Render FastAPI: yes
- AWB resources untouched: yes
- legacy Render DB/Redis preserved: yes
- no frontend secrets: verified by CI
- migrations green: yes
- security suite green: yes
- parity suite green: yes
- staging cutover verified: yes
- current operational production cutover verified using the shared environment: yes

Autonomous runtime execution remains disabled by design and is not part of F27 completion.
