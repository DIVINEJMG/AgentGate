# F27 staging provider status

## Completed

- F27.4: Neon staging schema is fully present in audoryn-staging.
  - Project: audoryn-staging
  - Region: aws-eu-central-1
  - Database: audoryn
  - Alembic version: 0003_f27_provider_metadata
  - Canonical, migration, artifact, audit, queue, and result tables verified.
  - WorkItem idempotency and artifact size/checksum metadata verified.
- F27.5: dedicated Upstash Redis staging database created in the current Aduoryn Upstash account.
  - Name: audoryn-redis-staging
  - Region: eu-central-1
  - TLS: enabled
  - Eviction: disabled
  - Live SET/GET verification passed.
- F27.6: Render audoryn-api REDIS_URL points to audoryn-redis-staging.
  - The legacy Render Redis instance remains intact for rollback.
  - The resulting Render deploy completed successfully.
- F27.7: private Upstash Blob bucket created in the same current Aduoryn Upstash account.
  - Name: audoryn-artifacts-staging
  - Visibility: private
  - A signed PUT upload of a staging health marker succeeded.
  - The application blueprint exposes server-only Blob configuration boundaries.
- F27.9/F27.10: QStash and Workflow are anchored in the current Aduoryn Upstash account.
  - EU QStash credentials are configured server-side in Render.
  - QStash and Workflow DLQs are reachable and currently empty.
  - The Render cron scheduler declaration is removed.
  - QStash HTTP dispatch is the target scheduling topology.
  - The Python scheduler module remains as a provider-independent fallback.
  - A signed POST heartbeat endpoint exists for safe QStash delivery verification.

## Current Upstash account rule

The currently connected Upstash account is the only active Upstash account for Aduoryn going forward:

- Redis: audoryn-redis-staging
- Blob: audoryn-artifacts-staging
- QStash: EU account
- Workflow and DLQ: same EU QStash account

Resources that were previously created under another Upstash account are historical only and must not
be modified, reused, deleted, or treated as active Aduoryn infrastructure.

## Blob runtime credential note

The connected Upstash tool deliberately does not return private bucket tokens. The bucket itself is
created and storage writes are verified through provider-signed URLs. Activating the FastAPI
ObjectStorage transport still requires the server-only UPSTASH_BLOB_TOKEN to be supplied through the
provider console or another secure credential path; it must never be committed to GitHub or exposed
to the frontend.
