# F27 Cutover

Cutover order:
1. Neon PostgreSQL.
2. Upstash Redis.
3. Upstash Blob.
4. FastAPI API.
5. QStash scheduling.
6. Results/artifacts.
7. Scheduler authority.
8. Runtime authority.
9. Integrations.

The active shared environment reached CUTOVER_STAGE=runtime after live QStash scheduling verification. RUNTIME_EXECUTION_ENABLED=false remains mandatory until runtime parity is explicitly approved.

## Current operational production mode

For now, the verified Aduoryn staging resources are also the production resources by explicit user decision.

This means the active operational stack is:
- audoryn-staging (Neon)
- audoryn-redis-staging
- audoryn-artifacts-staging
- QStash EU
- audoryn-api-staging on Render
- audoryn on Vercel

The separate-production-resource requirement from the original F27 plan is temporarily waived. Environment separation can be reintroduced later as a new infrastructure phase without changing the domain/provider boundaries established in F27.

## Safety state

- Runtime authority boundary: prepared.
- Autonomous runtime execution: disabled.
- Legacy Render PostgreSQL/Redis: rollback only.
- Older Render API: fallback only.
- AWB resources: untouched.
