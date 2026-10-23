# Aduoryn Python backend

This directory is the active Python backend introduced by F26.

The TypeScript implementation is preserved under `reference/typescript-backend/` and in the frozen
F25 checkpoint for behavioral parity evidence. Python/FastAPI is the migration target; domains are
considered complete only after contracts, persistence, security invariants, and parity checks pass.

## The core API and its packages (architecture plan, Phase 4)

This folder is the core API (`app`). With it go the shared code (`packages/shared`) and the
execution service (`services/execution`). See [service boundaries](../docs/architecture/service-boundaries.md).

Set up a development environment (from `backend/`):

```text
python -m venv .venv
.venv\Scripts\python -m pip install -e "../packages/shared[coding]" -e ../services/execution -e ".[dev]"
.venv\Scripts\playwright install chromium
```

`SERVICE_ROLE` picks what a process runs (`all`, the default, is everything in one process):

```text
uvicorn app.main:app                                                  (SERVICE_ROLE=all or api)
uvicorn audoryn_execution.service:create_application --factory        (the execution service)
```

The tests for all three packages are in `tests/` and run with `pytest`. The execution service also
has its own tests (`services/execution/tests`), which run without the core.

The shared package's dependencies are the libraries every service needs. No service names it as a
dependency, so install it together with the service, as above: pip then never looks for it on a
package index.
