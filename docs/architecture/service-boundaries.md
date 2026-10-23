# Service boundaries

AgentGate is the gateway plus three Python packages in this repository. The packages run as up to three kinds of process, chosen by `SERVICE_ROLE`. The details and history are in the [architecture plan](../plans/architecture-scale-plan.md), section 3 and Phases 3–4.

## The packages

| Package | Folder | Holds |
|---|---|---|
| Core API (`app`) | `backend/app` | people-facing routes and their services: auth, conversations, approvals, workers and jobs, live results, realtime, plans, integration sign-in, the Console channel; the migrations (`backend/migrations`) |
| Shared (`audoryn_shared`) | `packages/shared/audoryn_shared` | what both services use: settings, database models and session, domain, coordination (Redis leases), QStash dispatch and the outbox, the planner, the providers' setup and catalogues, observability, the application builder and lifespan |
| Execution (`audoryn_execution`) | `services/execution/audoryn_execution` | the step engine (`runtime/managed.py`), the provider executor, the governed browser's runtime (Playwright), preparations and their recovery, the QStash callback routes, and `service.py`, which joins them to the shared code |
| Gateway (`audoryn_gateway`) | `services/gateway` | the only public entry point: routing, health, draining, limits, CORS |

**Import rules.** Shared imports only shared code. The core imports shared code. The execution service imports shared code and its own. The core loads the execution service by name (`app/main.py`), and only for `SERVICE_ROLE=all` or `execution`.

**Enforced by:**
- **Core alone:** CI installs the core, and its tests run, without the execution service or Playwright (`core-alone` job, `tests/test_service_roles.py`).
- **Execution alone:** CI installs the execution service, and its tests run, without the core (`execution-alone` job, `services/execution/tests`).
- **The dispatch messages:** the messages between them are pinned in `contracts/dispatch-messages.json`. The senders' side is checked in `backend/tests/test_message_contracts.py` and the receivers' side in `services/execution/tests/test_message_contracts.py`.

## The processes

| Process | `SERVICE_ROLE` | Image | Serves | Background work |
|---|---|---|---|---|
| Core API | `api` | `backend/Containerfile.api` | the core routes | delivery watchdog (armed by its own commits; it asks the execution service to sweep) |
| Execution | `execution` | `services/execution/Containerfile` | QStash callbacks and direct due-step calls: `/internal/v1/runtime/*`, `/internal/v1/integrations/*`, `/internal/v1/web-research/*`, `/internal/v1/outbox/*` | task steps, browser and coding sessions, preparations and their recovery, delivery watchdog (full sweep) |
| Single service | `all` (default) | `backend/Containerfile` | everything, as before the split | everything |

**Where it is wired:**
- **Routes:** `app/api/router.py` (`CORE_ROUTERS`) and `audoryn_execution/service.py` (`ROUTERS`). The gateway's route table (`services/gateway/audoryn_gateway/routes.py`) sends the execution prefixes to the execution pool; `tests/test_service_roles.py` and `test_gateway_route_contract.py` fail if they disagree.
- **Building a process:** `audoryn_shared/bootstrap/application.py` (`build_application`) and `audoryn_shared/bootstrap/lifecycle.py`. The execution service's `start` and `stop` join the lifespan.
- **What only the execution service has:** importing `audoryn_execution.service` installs three things into the shared code:
  - running a due step here (`qstash_trigger.local_runner`);
  - the full sweep (`delivery_watchdog.sweep`);
  - the browser runtime (`browser_provider.use_runtime`).
- **A due step from the core:** it goes to the execution service as a signed direct call (`audoryn_shared/application/internal_calls.py`). QStash carries delayed steps and every fallback.

**How the processes talk:**
- **Core to execution:** a due step goes as a signed direct call through the gateway. Preparations, integration events, web research and drain requests are queued in the outbox, and QStash delivers them to the execution pool.
- **Execution to people:** outbox rows go on the Redis realtime bus, and the core's WebSocket delivers them.
- **Shared by all:** one Postgres database and one migration chain, and one Redis.

## Rules

- **One execution process at a time,** until browser sessions can move between processes. A browser task's session lives in the memory of the process that opened it.
- **Shared pending work is claimed:**
  - steps by a lease per step;
  - preparations by a lease per preparation;
  - outbox rows and approvals by `SKIP LOCKED`;
  - schedules by a row lock.

  So work that two processes both see is done once.
- **A new module goes where its importers are.** Code both services use is shared. A route goes into one of the two route lists and into the gateway's route table.
- **The GitHub provider is in shared code.** The core's GitHub sign-in and setup use it, and it loads its action modules eagerly. Splitting its setup side from its action side would let those move to the execution service; that is not done yet.
