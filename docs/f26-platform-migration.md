# F26 Platform Independence & Python Backend Migration

## F26.0–F26.5
The F25 reference is frozen, the frontend is Vercel-ready and AppDeploy-independent, and the FastAPI skeleton is established.

## F26.6–F26.10
- Python 3.13 baseline now includes FastAPI, Pydantic v2/settings, SQLAlchemy 2 async, Alembic, asyncpg, Redis, httpx, pytest/pytest-asyncio, Ruff and Pyright.
- Render is the backend target for API, PostgreSQL and Redis-compatible Key Value; scheduler/worker topology remains separated.
- Domain persistence uses repository protocols with SQLAlchemy implementations under infrastructure.
- PostgreSQL is the sole canonical state store.
- Alembic migration 0001 establishes the canonical relational schema with foreign keys, unique constraints, checks and indexes.
- Redis is strictly coordination: locks, leases/heartbeats, short-lived cache and pub/sub. Redis loss must never erase canonical business state.

Platform rule: GitHub is source of truth; Vercel hosts frontend; Render hosts backend/runtime; domain code depends on no hosting-platform SDK.
