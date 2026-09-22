# F26 Platform Independence & Python Backend Migration

## F26.0–F26.25
Platform extraction, Vercel frontend hosting, Python/FastAPI foundation, Render data resources,
PostgreSQL canonical state, Redis coordination, provider boundaries, Action Gateway, object storage,
separate runtime processes, API adapters, CORS/secrets, local development, migration tracking, and
the permanent security invariant suite are established.

## F26.26–F26.30

### F26.26 — Behavioral parity
Parity fixtures record security-sensitive TypeScript reference behavior and Python outcomes for
default-deny capability handling, no-policy default deny, and policy safety precedence. These run in
`tests/parity` and expand as domains are migrated.

### F26.27 — CI
CI explicitly runs Ruff, Pyright, full pytest, the security-invariant suite, parity suite, API
contract suite, and Alembic offline migration generation, in addition to frontend typecheck/build.
Deployment-contract validation is a separate workflow.

### F26.28 — GitHub → Vercel
The connected Vercel GitHub integration remains authoritative: PRs create previews and merges to
`main` create production deployments. GitHub validates that Vercel remains frontend-only.

### F26.29 — GitHub → Render
A backend deployment workflow waits for successful CI on `main` and triggers Render only when a
deploy hook is configured. With no paid Render compute currently provisioned, it skips cleanly rather
than claiming a deployment. PostgreSQL/Redis remain available.

### F26.30 — Data migration
A user-approved export extractor, normalizer, validator, staging repository, migration batch, and
legacy-ID mapping schema are established. Direct AppDeploy access is explicitly excluded.

Platform rule: GitHub is source of truth; Vercel hosts frontend; Render hosts backend/runtime; domain
code depends on no hosting-platform SDK.


## F26.31–F26.34

Shadow mode is read-only, cutover authority is explicit and progressive, active production paths are
CI-enforced to contain zero retired-platform dependencies, and the final production/rollback
architecture is documented. Render backend compute remains the final infrastructure prerequisite
before declaring the complete F26 production cutover done.
