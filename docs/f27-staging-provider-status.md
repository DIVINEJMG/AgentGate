# F27 staging provider status

## Completed

- F27.4: Neon staging schema is fully present in audoryn-staging.
  - Project: audoryn-staging
  - Region: aws-eu-central-1
  - Database: audoryn
  - Alembic version: 0003_f27_provider_metadata
  - Canonical, migration, artifact, audit, queue, and result tables verified.
  - WorkItem idempotency and artifact size/checksum metadata verified.
- F27.5: dedicated Upstash Redis staging database created.
  - Name: audoryn-redis-staging
  - Region: eu-central-1
  - TLS: enabled
  - Eviction: disabled
  - Existing AWB Redis resources were not reused or modified.
- F27.6: Render audoryn-api REDIS_URL now points to audoryn-redis-staging.
  - The Render Redis instance remains intact as rollback infrastructure.
  - The resulting Render deploy completed successfully.
  - Direct Redis SET/GET health verification passed.
- F27.7: private Upstash Blob bucket exists from the earlier provider account:
  - audoryn-artifacts-staging
  - The newly connected Upstash account is separate and does not expose that bucket.
  - No existing bucket was deleted, replaced, or recreated.
- F27.10: Render cron scheduler declaration removed.
  - QStash HTTP dispatch is now the target scheduling topology.
  - The Python scheduler module remains as a provider-independent fallback.
  - The newly connected Upstash account currently has no QStash schedules; future schedules must be
    created in the account selected for Aduoryn scheduling.

## Account isolation note

The current Upstash connection is a different account from the one used to create the original
audoryn-artifacts-staging Blob bucket. Redis cutover uses the current account. Existing resources in
the previous account remain untouched.
