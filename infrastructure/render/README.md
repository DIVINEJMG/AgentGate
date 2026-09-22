# Render backend target

F26.7 establishes Render as Aduoryn's backend/runtime platform.

Resources:
- audoryn-api: FastAPI web service
- audoryn-postgres: canonical PostgreSQL state
- audoryn-redis: ephemeral Redis-compatible coordination
- audoryn-scheduler: scheduled process
- audoryn-worker: background-worker target for F26.17/F26.18

The application uses DATABASE_URL and REDIS_URL; no Render SDK is permitted in domain code.
