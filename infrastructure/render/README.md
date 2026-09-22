# Render backend target

F26.7 establishes Render as Aduoryn's backend/runtime platform.

## Provisioned in Frankfurt
- `audoryn-postgres`: canonical PostgreSQL state (free plan)
- `audoryn-redis`: ephemeral Redis-compatible coordination (free plan, persistence off, noeviction)

## Compute topology prepared in Git
- `audoryn-api`: FastAPI web service
- `audoryn-scheduler`: scheduled process
- `audoryn-worker`: background-worker target for F26.17/F26.18

Render's current API rejected the free compute plan for web/cron services. No paid service was
created automatically. The compute definitions remain deployment-ready in Git until a Render
compute plan is explicitly selected.

The application uses `DATABASE_URL` and `REDIS_URL`; no Render-specific SDK is permitted in
domain code. PostgreSQL remains canonical even when Redis is unavailable.
