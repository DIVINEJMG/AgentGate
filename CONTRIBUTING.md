# Contributing to Audoryn

## Repository culture

Audoryn evolves through explicit foundations and small reviewable changes.

### Branches

Use purpose-driven branches such as `foundation/02-identity-organizations`, `feature/policy-condition-time`, or `fix/v1-status-adapter`.

### Commits

Prefer conventional, scoped commits such as `feat(api): add v2 status adapter` or `docs(adr): record identity boundary decision`.

### Compatibility

Never silently break a supported API version. Add adapters or introduce a new version, publish its lifecycle state, migrate consumers, then retire the old contract deliberately.

### Database evolution

Once a production migration ships, do not rewrite it. Add a new migration.

Change the schema in two releases (expand, then contract), because two versions of the code can run at once during a rolling deploy:

1. **Expand.** Add the new table, column or index, nullable or with a default. Ship code that works with both the old and the new shape.
2. **Contract.** Once no running version uses the old shape, remove it in a later release.

Never rename or drop something that the running version still reads, and never add a required column without a default in the same release.

### Where code goes

AgentGate's Python code is three packages:
- the core API, `backend/app`;
- the shared code, `packages/shared/audoryn_shared`;
- the execution service, `services/execution/audoryn_execution`.

[Service boundaries](docs/architecture/service-boundaries.md) has the details.

**Rules for new code:**
- **Shared code never imports the core or the execution service,** and those two never import each other. CI checks this by installing each service on its own (`core-alone`, `execution-alone`).
- **A module goes where its importers are.** Code both services use is shared.
- **A new route goes into one of the two route lists,** and into the gateway's route table.
- **A message to the execution service is a contract:** it changes together with `contracts/dispatch-messages.json` and the tests on both sides.

### Shutdown and restarts

A restart must not break running work. On shutdown, the backend lets running steps finish for up to `RUNTIME_DRAIN_SECONDS`, then cancels the rest. A cancelled step keeps its checkpoint, and the sweep recovers it. The process manager's stop timeout must be longer than the drain. Prove a change to this path with `backend/scripts/restart_drill.py` (see [docs/testing/ai-simulation.md](docs/testing/ai-simulation.md)).
