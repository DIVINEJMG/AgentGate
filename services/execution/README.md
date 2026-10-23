# AgentGate execution service

The background work of AgentGate (architecture plan, Phases 3 and 4):
- task steps (`runtime/managed.py`) and the provider executor;
- the governed browser's runtime (Playwright and Chromium) and coding sessions;
- preparations of saved requests and their recovery;
- the QStash callback routes: `/internal/v1/runtime/*`, `/internal/v1/integrations/*`,
  `/internal/v1/web-research/*` and `/internal/v1/outbox/*`.

It runs with the shared code (`packages/shared`) and without the core API. Where everything
lives, and the rules between the packages, are in [service boundaries](../../docs/architecture/service-boundaries.md).

## How it joins the shared code

`audoryn_execution/service.py` is the only joining point. Importing it installs, into the shared code:
- the step runner, so a process that runs steps starts a due step itself (`qstash_trigger.local_runner`);
- the full sweep, for the delivery watchdog;
- the browser runtime, for the browser provider.

Its `start` and `stop` join the shared lifespan:
- **`start`:** the preparation recovery.
- **`stop`:** the step drain (`RUNTIME_DRAIN_SECONDS`), then the preparations, then the providers' runtimes. It runs after the process has stopped taking local steps.

## Running it

```text
pip install -e "packages/shared[coding]" -e "services/execution[dev]"    (from the repository root)
playwright install chromium
SERVICE_ROLE=execution uvicorn audoryn_execution.service:create_application --factory --port 8201
```

**The image:** `docker build -f services/execution/Containerfile .` from the repository root. It keeps the repository's layout.

**Settings:** the same as the backend. `SERVICE_ROLE=execution`, and the same `INTERNAL_CALL_KEY_ID` and `INTERNAL_CALL_KEY_SECRET` as the core API, so its direct due-step calls are accepted.

## Tests

`pytest` here checks:
- **On its own:** the service builds with the core API absent, and serves only its own routes.
- **The dispatch messages:** its message models accept exactly `contracts/dispatch-messages.json`.

The whole behaviour is tested in `backend/tests`, which runs all three packages together. CI runs both:
- the `execution-alone` job installs this service without the core;
- the `backend` job runs everything.

## Drain and restart

Restart it with the gateway's rolling restart (`python -m audoryn_gateway.rollout --pause-last execution`). It is the only copy of the execution pool, so that pool pauses during the restart:
- QStash retries its deliveries;
- running steps finish within `RUNTIME_DRAIN_SECONDS`.
