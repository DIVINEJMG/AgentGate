# F26 production architecture

## Target topology

```text
Internet
   |
   v
Vercel
React + TypeScript
   |
   | HTTPS / WSS
   v
Render
FastAPI API
   |
   +-------------------+-------------------+
   |                   |                   |
PostgreSQL           Redis            Object Storage
   ^                   ^
   |                   |
   +---------+---------+
             |
      +------+------+
      |             |
    Worker       Scheduler
      |
      v
 Action Gateway
      |
  +---+---+---+---+
  |       |       |
GitHub  Gmail   Slack   future browser executor
```

## Authority rules

- GitHub is the only source of truth for application code.
- Vercel owns the browser/frontend deployment.
- Render is the backend/runtime target.
- PostgreSQL is canonical business state.
- Redis is non-authoritative coordination.
- Object storage is accessed only through the `ObjectStorage` boundary.
- Human identity and Agent identity remain separate.
- External side effects execute only through the Action Gateway.
- API v1 and v2 adapt into canonical application/domain behavior.

## Cutover controls

`CUTOVER_STAGE` advances authority in this order:

`frontend → system → authentication → organizations → workforce → jobs → results → governance → scheduler → runtime → integrations`

`SHADOW_MODE_ENABLED=true` allows deterministic/read-only comparison only. It never duplicates provider writes.

## Current infrastructure reality

The code and deployment definitions are ready for the target topology. Render PostgreSQL and Redis
are provisioned. Render API/worker/scheduler compute is not currently provisioned because available
compute plans require billing. Until compute is explicitly enabled, the backend deployment workflow
skips safely and Python runtime execution remains disabled.

Therefore F26.31–F26.34 finalization is implemented in code, but the overall F26 production cutover
must not be declared complete until Render compute, backend secrets, migrations, health checks, and
the staged cutover have been exercised in production.
