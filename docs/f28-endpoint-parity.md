# F28 Endpoint Parity Audit

F28 makes endpoint parity a release gate for the Python migration.

The active product architecture remains:

```text
React + TypeScript (Vercel)
        |
        v
FastAPI (Render)
        |
        v
Canonical Python domain
   |          |
 Neon      Upstash
 truth    coordination
```

## F28.0 baseline

Production snapshot when F28.0 started:

- Vercel production commit: `3c877017b747eeb8eaa8e58791995c1a264c9118`
- Render production commit: `3c877017b747eeb8eaa8e58791995c1a264c9118`
- TypeScript behavioral reference: `reference/typescript-backend/`
- Python public API: `backend/app/api/`
- Frontend API contracts: `frontend/src/lib/*Api.ts`

The TypeScript reference is read-only migration evidence and must not be deleted before F28 completes.

## Classification

Every frontend contract is classified as one of:

- **WORKING** — Python route exists and the contract has been live-verified.
- **PARTIAL** — Python route exists, but complete live/product parity verification is still pending.
- **MISSING** — the frontend expects the contract but the active Python API does not expose it.
- **LEGACY ONLY** — present in the TypeScript reference but no longer requested by the current frontend and not implemented in Python.

`scripts/audit_f28_endpoints.py` is the executable inventory source. It discovers:

1. current frontend API calls,
2. TypeScript reference routes,
3. active FastAPI routes,
4. normalized v1/v2 contracts,
5. migration status.

Run:

```bash
cd backend
python ../scripts/audit_f28_endpoints.py
```

CI runs the audit with `--check` so the inventory cannot silently disappear while F28 is in progress.

## Initial finding

The frontend currently contains product clients for substantially more domains than the active public Python API.

The active Python public surface currently includes:

- health/readiness,
- v1/v2 system status,
- v2 signup/login/session/logout,
- v1/v2 organization list/create.

The frontend additionally expects contracts across:

- Agent Identity,
- Workforce,
- Jobs and Work Items,
- Integrations,
- Capabilities,
- Policy,
- Risk,
- Action Gateway,
- Approvals,
- Incidents and execution controls,
- Audit,
- Scheduler/triggers,
- Runtime/runs,
- Memory,
- Artifacts,
- Results,
- Commercial,
- Product/workspace settings,
- Templates/quick start,
- Supervision/escalations,
- Performance.

These domains are therefore not considered migrated merely because infrastructure health and authentication succeed.

## F28.0 release rule

A frontend contract is not considered migrated until its canonical Python implementation, authorization, tenant boundary, persistence behavior, error contract, tests, and live Render behavior have been verified.

F28.0 itself does not make CI fail because routes are still missing; it makes missing routes visible and machine-discoverable. F28.9 will turn endpoint completeness into a hard gate once the domain migration reaches that checkpoint.

## F28.1 boundary guard

`scripts/verify_f28_architecture.py` prevents the Python domain layer from importing FastAPI or concrete infrastructure/provider modules.

The dependency direction remains:

```text
API -> Application -> Domain
                    ^
                    |
              Infrastructure
```

Infrastructure implements domain/application contracts; domain code never reaches outward into provider SDKs.
