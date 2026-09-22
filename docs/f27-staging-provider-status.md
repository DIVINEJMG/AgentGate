# F27 staging provider status

## Completed

- F27.4: Neon staging schema is fully present in audoryn-staging.
  - Project: audoryn-staging
  - Region: aws-eu-central-1
  - Database: audoryn
  - Alembic version: 0003_f27_provider_metadata
  - Canonical, migration, artifact, audit, queue, and result tables verified.
  - WorkItem idempotency and artifact size/checksum metadata verified.
- F27.7: private Upstash Blob bucket created:
  - audoryn-artifacts-staging
- F27.10: Render cron scheduler declaration removed.
  - QStash HTTP dispatch is now the target scheduling topology.
  - The Python scheduler module remains as a provider-independent fallback.

## Redis capacity blocker

Upstash write access is working, but the account's free plan currently permits only one Redis
database. The existing slot is occupied by awb-gateway-staging-tls.

Per the infrastructure isolation rule, that AWB database is not reused, deleted, reset, or renamed.

Therefore:
- F27.5 remains blocked until an additional Upstash Redis database can be provisioned.
- F27.6 remains intentionally on the existing Render Redis until F27.5 is resolved.

No billing change is performed automatically.
