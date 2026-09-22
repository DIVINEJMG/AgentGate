# F26 Platform Independence & Python Backend Migration

## F26.0–F26.20
Platform extraction, Vercel frontend hosting, Python/FastAPI foundation, Render data resources,
PostgreSQL canonical state, Redis coordination, identity/model/integration boundaries, Action Gateway,
object storage abstraction, separate API/worker/scheduler processes, v1/v2 adapters, and frontend/API
separation are established.

## F26.21–F26.25

### F26.21 — CORS
FastAPI now uses an explicit origin allowlist. Production configuration rejects wildcard origins.
Local development defaults to `http://localhost:5173`.

### F26.22 — Secrets
Backend credentials use Pydantic `SecretStr` and are loaded only from environment/secret stores.
Database, Redis, OIDC, model-provider, integration-encryption and object-storage secrets are backend
only. Browser-safe configuration remains limited to `VITE_API_BASE_URL` and future explicitly
public auth metadata.

### F26.23 — Local development
`infrastructure/local/docker-compose.yml` provides PostgreSQL, Redis and MinIO. The backend and
frontend can run locally without Render or Vercel.

### F26.24 — Domain-by-domain migration
`docs/f26-domain-migration-matrix.md` records the ordered migration status. A domain is not complete
until contracts, persistence, security invariants and parity checks pass; the TypeScript reference is
retained until then.

### F26.25 — Permanent security invariant suite
CI now runs a dedicated invariant suite covering identity separation, non-authoritative
instructions/memory, policy precedence, tenant separation and side-effect idempotency. Existing
schema constraints preserve hashed agent credentials rather than plaintext secrets. Additional
invariants are added as approval/incident/runtime domains complete.

Platform rule: GitHub is source of truth; Vercel hosts frontend; Render hosts backend/runtime; domain
code depends on no hosting-platform SDK.
