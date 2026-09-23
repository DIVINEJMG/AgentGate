# F28.21–F28.26 — Final Python migration and cutover foundation

## F28.21 — Local development environment

Aduoryn now has a cloud-independent local foundation using Docker Compose:

- PostgreSQL
- Redis
- MinIO-compatible object storage

The backend remains runnable with local environment defaults and the frontend remains a separate Vite process.

## F28.22 — CI gates

CI requires:

- Ruff lint
- Ruff format check
- Pyright
- full pytest suite
- explicit security invariant suite
- parity suite
- API contract suite
- Alembic offline migration check
- all F27/F28 architecture verification scripts
- frontend typecheck and build

Python-vs-TypeScript parity remains mandatory while the preserved TypeScript tree is migration evidence.

## F28.23 — Data migration strategy

`RepeatableMigrationPipeline` formalizes extract/normalize/validate/stage/mapping behavior. It rejects:

- unsupported entity types
- blank legacy IDs
- duplicate source/entity/legacy identities

Every mapping retains `legacy_id`, `new_id`, `source_system`, and `migrated_at`.

Canonical promotion remains a separate, explicitly enabled operation. Production data is never copied ad hoc.

## F28.24 — Shadow mode

Shadow comparison remains read-only/deterministic. The Python candidate and TypeScript reference may be compared for parity, but `ShadowModeService` refuses duplicated side effects.

## F28.25 — Progressive cutover

Authority continues through the existing ordered `CutoverController`:

frontend → system → authentication → organizations → workforce → jobs → results → governance → scheduler → runtime → integrations.

A domain is not authoritative merely because its Python implementation exists.

## F28.26 — Final Python cutover

`FinalCutoverGate` blocks runtime/integration authority unless all required evidence is green:

- parity passed
- security invariants passed
- migration validated
- rollback documented
- frontend verified

The preserved TypeScript backend remains reference evidence for at least one release cycle. It is not an active production writer or rollback platform.

Runtime execution remains independently controlled by `RUNTIME_EXECUTION_ENABLED`; completing this checkpoint does not automatically enable autonomous execution.
