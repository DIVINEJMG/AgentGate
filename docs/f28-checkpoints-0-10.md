# F28.0–F28.10 Completion Record

This document records the architecture and migration checkpoints that must be true before
Aduoryn continues into F28.11+ domain-by-domain endpoint migration.

The target remains:

```text
React + TypeScript
        |
        v
FastAPI
        |
        v
Canonical Python Domain
   |        |        |
 Neon    Upstash   Blob
 truth   coordination artifacts
```

The TypeScript implementation under `reference/typescript-backend/` remains read-only
behavioral reference material until the full F28 migration is complete.

## F28.0 — Endpoint/reference audit

Implemented by:

- `scripts/audit_f28_endpoints.py`
- `docs/f28-endpoint-parity.md`

The audit discovers current frontend requests, TypeScript reference routes, and FastAPI
OpenAPI routes and classifies the current migration surface.

Status: **complete**.

## F28.1 — Python layering

Dependency direction:

```text
API -> Application -> Domain
                    ^
                    |
              Infrastructure
```

`scripts/verify_f28_architecture.py` prevents the domain layer from importing FastAPI,
SQLAlchemy, Redis, provider SDKs, or concrete infrastructure implementations.

Status: **complete and CI enforced**.

## F28.2 — Python technology baseline

Canonical backend baseline:

- Python 3.13
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x async
- Alembic
- Neon PostgreSQL
- Upstash Redis
- Upstash QStash
- Upstash Blob
- pytest / pytest-asyncio
- httpx
- Ruff
- Pyright

Frontend remains React + TypeScript + Vite.

`scripts/verify_f28_baseline.py` verifies required dependencies and rejects active
AppDeploy runtime imports from Python production code.

Status: **complete and CI enforced**.

## F28.3 — Neon is canonical persistent truth

The SQLAlchemy metadata contains the canonical persistent tables required by F28,
including identity, workforce, jobs, runtime, integrations, capabilities, policy,
risk, approvals, incidents, memory, artifacts, results, audit, and outbox state.

`scripts/verify_f28_persistence.py` verifies required tables and critical relational
lineage such as:

- Membership -> Organization / Human
- Agent -> Organization
- Worker -> Agent
- Job -> Worker
- WorkItem -> Job
- Run -> WorkItem
- Approval -> Action
- Result -> Worker / Job

F28 Agent Identity uses the existing Neon Agent/credential schema rather than an in-memory
product store. Schema expansion that requires table-owner DDL is intentionally deferred to
F28.11+, where domain-by-domain migration begins; the Render application role remains
non-owner and is not granted DDL authority.

Status: **complete as the canonical persistence contract**. F28.11+ owns schema expansion
and ports remaining product endpoints onto this schema.

## F28.4 — Redis is coordination-only

Redis remains restricted to ephemeral coordination such as sessions, failed-login
rate limiting, leases, locks, heartbeats, scheduler/runtime coordination, caching,
and short-lived execution state.

`scripts/verify_f28_redis_boundaries.py` rejects canonical business-state key prefixes
for Workers, Jobs, Results, Policies, Approvals, Audit, Capabilities, Memberships,
Organizations, and Agents.

Status: **complete and CI enforced**.

## F28.5 — Authentication / IdentityProvider boundary

Human authentication remains:

```text
Email/password
   -> Neon human identity + credential
   -> Upstash opaque session
   -> RedisSessionIdentityProvider
   -> HumanPrincipal
```

Implemented behavior includes:

- signup
- login
- session lookup
- logout / revocation
- TTL-based session expiry
- failed-login rate limiting
- organization membership resolution
- RBAC resolution
- cross-tenant rejection

`RedisSessionIdentityProvider` implements the replaceable Human identity boundary.
Future OIDC/SAML providers can replace the adapter without changing product domains.

Status: **complete**.

## F28.6 — Human Identity != Agent Identity

Human and Agent authentication paths are separate.

Human:

```text
Bearer session -> HumanPrincipal
```

Agent:

```text
Agent credential -> AgentCredentialAuthenticator -> AgentPrincipal
```

Agent credentials are hashed, versioned, revocable, tenant-bound, and capability-aware.
A Human bearer token cannot pass the Agent authentication parser, and an Agent secret
cannot pass the Human bearer parser.

Agent Identity endpoints preserve registration, lifecycle, rotation, revocation,
permissions, tenant isolation, and audit events across v1 and v2.

Status: **complete and security-tested**.

## F28.7 — Canonical domain models

`app/domain/canonical.py` defines provider-independent canonical domain objects for:

- Organization
- Membership
- AgentIdentity
- AgentCredential
- Worker
- Job / JobRevision
- WorkItem
- Integration
- Capability
- Policy / PolicyDecision
- RiskAssessment
- ActionProposal / ActionDecision
- Approval
- Incident
- Run / RunStep
- Memory
- Artifact
- Result
- AuditEvent

Pydantic remains the API boundary validator. Domain objects do not import provider
implementations.

Status: **complete**.

## F28.8 — Action Gateway

All external side-effect architecture remains behind `ActionGateway`.

The gateway enforces:

- Agent identity
- tenant match
- Agent identity match
- declared capability
- stable idempotency key
- guard decisions
- deny behavior
- approval-required behavior

`scripts/verify_f28_action_gateway.py` prevents provider SDK imports from API,
Application, and Domain layers and verifies the core gateway invariants remain present.

Status: **complete and CI enforced**.

## F28.9 — v1/v2 compatibility gate

The canonical domain never branches on API version. v1/v2 differences stay in API
adapters/routes.

`scripts/verify_f28_api_parity.py` examines frontend versioned contracts and prevents
a migrated endpoint from existing in only one Python API version.

`scripts/audit_f28_endpoints.py` remains the full migration inventory. Endpoints still
classified MISSING are migrated in F28.11+; once a contract begins migration, CI prevents
one-sided v1/v2 drift.

Status: **compatibility gate complete**. Remaining domain endpoint implementation is the
explicit F28.11+ migration workload.

## F28.10 — Canonical service contracts

`app/application/ports.py` defines stable interfaces for:

- OrganizationRepository
- MembershipRepository
- AgentRepository
- WorkerRepository
- JobRepository
- WorkItemRepository
- IntegrationRepository
- CapabilityRepository
- PolicyRepository
- RiskRepository
- ApprovalRepository
- IncidentRepository
- ActionRepository
- AuditRepository
- MemoryRepository
- ResultRepository
- ObjectStorage
- CacheStore
- QueueBroker
- SecretVault
- IdentityProvider
- IntegrationAdapter

`scripts/verify_f28_contracts.py` keeps this contract set present in CI.

Infrastructure can therefore remain replaceable while the core stays stable.

Status: **complete and CI enforced**.

## F28.0–F28.10 exit condition

F28.11+ may proceed only while all F28 CI gates remain green:

```text
F28 architecture
F28 endpoint inventory
F28 technology baseline
F28 Neon persistence
F28 Redis boundary
F28 Action Gateway
F28 paired v1/v2 migration
F28 canonical contracts
security invariants
contract tests
parity tests
Ruff
Pyright
pytest
frontend typecheck/build
```

Automatic Vercel Git deployments are paused during this backend-heavy migration to
avoid unnecessary deployment usage. They should be intentionally re-enabled when a
frontend production deployment is actually required.
