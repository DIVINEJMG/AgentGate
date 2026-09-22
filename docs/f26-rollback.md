# F26 rollback

Rollback is stage-based, not a return to the retired hosting platform.

## Principles

1. Never roll canonical PostgreSQL state backward by restoring an obsolete platform database.
2. Roll application releases backward only to a migration-compatible Git commit.
3. Disable runtime execution before rolling back worker/runtime behavior.
4. Lower `CUTOVER_STAGE` to the last verified authoritative stage.
5. Keep Redis disposable; rebuild coordination state from PostgreSQL.
6. Provider side effects already completed externally are not automatically reversible.

## Procedure

1. Set `RUNTIME_EXECUTION_ENABLED=false`.
2. Move `CUTOVER_STAGE` back to the previous verified stage.
3. Stop or disable worker and scheduler when runtime is affected.
4. Redeploy the last known-good GitHub commit to Render.
5. Keep the current PostgreSQL schema unless a downgrade has been explicitly tested.
6. Verify `/health/live`, `/api/v1/system/status`, and `/api/v2/system/status`.
7. Re-run security invariants, API contracts, and parity checks.
8. Re-enable later stages only after verification.

The TypeScript tree under `reference/` remains parity evidence and historical behavior. It is not a
production rollback target.
