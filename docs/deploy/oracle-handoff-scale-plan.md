# Oracle handoff: architecture for production scale

Plan: [docs/plans/architecture-scale-plan.md](../plans/architecture-scale-plan.md). Every phase is proven locally first. Oracle commands (in the A–F format) are written only when a phase reaches Phase 5.

## Phase 0: shutdown drain (built 2026-10-22, branch `feat/scale-phase0-drain`)

**What changed:**
- **New setting `RUNTIME_DRAIN_SECONDS`** (default `0`, at most 900). On shutdown, running steps get this long to finish before they are cancelled; the sweep recovers a cancelled step.
- **No migration, no new dependency, no new endpoint.**
- **A new local-only script, `backend/scripts/restart_drill.py`.** It is never run on Oracle.

**Oracle action now:** none. With the default of `0`, a restart behaves exactly as before. Deploying this branch is a normal deploy.

**When the drain is turned on** (Phase 5, its own A–F block):
- `RUNTIME_DRAIN_SECONDS` goes into `/etc/audoryn/audoryn.env`, backed up first.
- The container's stop timeout must be longer than the drain (`podman run --stop-timeout`, and `TimeoutStopSec` on the `audoryn-api.service` unit). Otherwise podman kills the process mid-drain.
- A deploy then takes up to the drain time to stop the old container. That wait is the running steps finishing.

## Phase 1a: the Audoryn Gateway in pass-through (built 2026-10-22, same branch)

**What changed:**
- **A new service folder, `services/gateway/`** (package `audoryn_gateway`), with tests and its own CI job.
- **No change to the backend's behaviour, settings, image or dependencies.**

**Oracle action now:** none. The gateway is not deployed yet. Deploying this branch changes nothing on Oracle, because `backend/Containerfile` builds only `backend/`.

**When the gateway goes to Oracle** (Phase 5, its own A–F block), each step needs a decision and its own rollback:
- an image for the gateway;
- `audoryn-api` moving to a private port;
- the gateway taking the public port.

## Phase 1b: routing, copies, health and draining (built 2026-10-22, same branch)

**What changed:**
- **The gateway** gains a route table, pools of copies, liveness checks, failover and the admin drain and restore controls.
- **The backend** gains one test (`tests/test_gateway_route_contract.py`) and a pyright `extraPaths` entry so that test resolves the gateway package. Its runtime behaviour is unchanged.

**Oracle action now:** none. The gateway is still not deployed.

**When it goes to Oracle** (Phase 5):
- `GATEWAY_ADMIN_TOKEN` goes in the gateway's env file only. It is never committed or logged.
- The admin controls answer on loopback only, so drains are run on the VM itself.
- The rolling restart order is drain → open requests 0 → graceful stop → start → restore → **back in rotation** → next copy.

## Phase 1c: traffic limits and shared drains (built 2026-10-22, same branch)

**What changed:**
- **The gateway** gains route-group limits (`off` / `report` / `enforce`, default `off`), body caps, and drains shared through Redis.
- **The gateway package** now lists `redis` (the backend's existing library and version range).
- **The backend is unchanged.**

**Oracle action now:** none. The gateway is still not deployed.

**When it goes to Oracle** (Phase 5):
- Set `GATEWAY_REDIS_URL`, `GATEWAY_REDIS_PREFIX` (one per environment) and `GATEWAY_LIMIT_SECRET` (the same value on every gateway copy) in the gateway's env file only.
- Start with `GATEWAY_LIMITS_MODE=report`. Read the `gateway.limit` lines, and move to `enforce` group by group.
- The gateway must sit next to its Redis, because under `enforce` every request waits one round trip.

**Open before production:** the intermittent step stall on the QStash callback path. See the plan, Phase 1c.

## Backend fix: credential lock held across provider calls (2026-10-22, same branch)

**What changed:**
- `app/execution/provider_executor.py`: the action's loading transaction (credential row lock, token scoping or renewal) is committed before the provider is called, instead of at the end of the step. One new test.
- No migration, setting or dependency.

**Effect on Oracle:** this is a normal backend deploy, and it **does** change the running backend. Tasks on the same GitHub connection no longer wait on each other's GitHub calls. Nothing to configure.

## Phase 1d: CORS and security headers at the gateway, as switches (2026-10-22, same branch)

**What changed:**
- **Core:** new setting `CORS_ENABLED` (default `true`). The CORS rules moved into one constant, so behaviour is unchanged.
- **Gateway:** `GATEWAY_CORS_ENABLED`, `GATEWAY_CORS_ALLOWED_ORIGINS`, `GATEWAY_SECURITY_HEADERS` and `GATEWAY_HSTS_SECONDS`, all off by default.

**Oracle action now:** none. Deploying this branch changes nothing about CORS.

**When the gateway fronts production** (Phase 5):
- Flip **as a pair, in one deploy:** `GATEWAY_CORS_ENABLED=true` with the core's `CORS_ALLOWED_ORIGINS` value, and the core's `CORS_ENABLED=false`. Back up the env files first.
- Rollback is the reverse pair.
- Turn on `GATEWAY_SECURITY_HEADERS`. Set `GATEWAY_HSTS_SECONDS` only once the public address is served over TLS.

## Phase 1e: traffic limits set in the Console (2026-10-22)

**What changed:**
- **AgentGate** (branch `feat/scale-phase0-drain`):
  - a new channel operation, `POST /internal/console/entitlements/v1/traffic-limits`, and `trafficLimitsVersion` in `GET /status`;
  - a new setting, `GATEWAY_REDIS_PREFIX` (default `audoryn:gateway`), naming the Redis prefix the gateway reads;
  - no migration and no new dependency.
- **Console** (branch `feat/gateway-traffic-limits`):
  - the platform-configuration key `gateway_traffic_limits`;
  - sending on save, and resending in the delivery worker's reconcile;
  - no migration.
  - The branch is based on the Console's `origin/main` (`1ee61c0`).
- **Gateway:** it follows the document. It is not deployed yet.

**Oracle action now:** deploying both branches is a normal deploy. Until someone saves the key in the Console, nothing is sent and nothing changes.
- If the Console's delivery worker runs before AgentGate has the new route, the reconcile's send gets a 404. It is logged, nothing breaks, and the next reconcile retries.
- **So deploy AgentGate first, then the Console.**

**When the gateway goes to Oracle** (Phase 5):
- `GATEWAY_REDIS_PREFIX` must hold the same value in the AgentGate env and the gateway env, and both must use the same Redis (AgentGate's `REDIS_URL` is the gateway's `GATEWAY_REDIS_URL`).

## Phase 2: several core copies and the rolling restart (2026-10-22, branch `feat/scale-phase0-drain`)

**What changed:**
- **Backend:**
  - each preparation (planning a saved request) runs under a Redis lease renewed while it runs, so two copies never prepare the same request; a copy that stops without releasing frees it within 90 s;
  - the approval sweep locks the approvals it reminds about or expires (`FOR UPDATE SKIP LOCKED`), so two copies never post the same reminder twice;
  - the lease heartbeat moved to `app/infrastructure/redis/coordination.py` (`keep_leases`), shared by runtime steps and preparations; behaviour unchanged.
- **Gateway:** `POST /gateway/admin/copies/{name}/drain?last=true`, the copies listing names each copy's pools, and the rolling restart tool `python -m audoryn_gateway.rollout`.
- No migration, no new dependency, no new setting.

**Oracle action now:** none. With one copy, nothing behaves differently: the lease is always free and the lock never meets another sweep. Deploying this branch is a normal deploy.

**When a second copy goes to Oracle** (Phase 5, its own A–F block):
- The first copy keeps running steps. Every other copy gets `RUNTIME_RUN_LOCAL=false`.
- The gateway's `GATEWAY_EXECUTION_UPSTREAMS` lists the first copy only. A browser task's session lives in the memory of the copy that opened it, so its steps must stay on that copy until Phase 3.
- Restarts go through `python -m audoryn_gateway.rollout --restart "<the command that restarts one copy>" --pause-last execution` on the gateway's machine. While the step-running copy restarts, QStash's deliveries get 502 and QStash retries them.
- **Rollback:** stop the second copy and remove it from `GATEWAY_CORE_UPSTREAMS`.

## Phase 3: the execution process (2026-10-22, branch `feat/scale-phase0-drain`)

**What changed (backend only; no migration, no new dependency):**
- `SERVICE_ROLE` (`all` by default, as today): `api` mounts the people-facing routes and never runs steps; `execution` mounts the QStash callbacks and runs steps, browser and coding sessions, and preparations.
- `EXECUTION_CALL_URL` and `INTERNAL_CALL_KEY_ID` / `_SECRET` (plus `INTERNAL_CALL_PREVIOUS_KEY_ID` / `_SECRET` for rotation): an `api` process sends a due step straight to the execution service, signed, instead of a QStash message. Refused or unreachable, it goes through QStash as before.
- `/internal/v1/runtime/execute` accepts QStash's signature or that internal signature; anything else is still a 401.

**Oracle action now:** none. With the defaults (`SERVICE_ROLE=all`, no key, no call URL), the backend behaves exactly as before. Deploying this branch is a normal deploy.

**When the execution process goes to Oracle** (Phase 5, its own A–F block):
- One more container from the same image with `SERVICE_ROLE=execution`. The existing backend gets `SERVICE_ROLE=api` and `EXECUTION_CALL_URL=<the gateway's internal address>`.
- Every backend container gets the same new internal key (generated on the laptop, put in each env file, never committed).
- The gateway's `GATEWAY_EXECUTION_UPSTREAMS` points at the execution container. QStash's URLs already point at the gateway, so they don't change.
- Chromium and Playwright are needed only in the execution container; until Phase 4 both use the same image.
- **Rollback:** set `SERVICE_ROLE=all` on the backend, point `GATEWAY_EXECUTION_UPSTREAMS` back at it, and stop the execution container.

## Phase 4: three packages and their images (2026-10-22, branch `feat/scale-phase0-drain`)

**What changed:**
- **The code moved, and its behaviour did not.**
  - `backend/app` keeps the core API.
  - The code both services use moved to `packages/shared/audoryn_shared`.
  - The execution service's code moved to `services/execution/audoryn_execution`.
  - Imports were rewritten by script and checked by the full suite, pyright, the verify scripts and a simulator run.
- **Dependencies:**
  - the libraries are now declared in `packages/shared/pyproject.toml`;
  - Playwright is declared by the execution service only;
  - E2B stays the shared `coding` extra.
  - No version changed and no new library was added.
- **Images, built from the repository root:**
  - `backend/Containerfile`: the single service, as today. **Its working folder is now `/app/backend` (was `/app`)**; the start command, port and user are unchanged.
  - `backend/Containerfile.api`: the core API alone, with no Playwright and no Chromium.
  - `services/execution/Containerfile`: the execution service alone.
- **Gateway** (not deployed yet):
  - idle connections to the copies end after 3 s (`GATEWAY_UPSTREAM_KEEPALIVE_SECONDS`) and from callers after 75 s (`GATEWAY_KEEP_ALIVE_SECONDS`);
  - a safe request whose connection dropped is retried on the same copy when it is the only one in rotation.
  - Found under load: equal 5-second limits on both sides of a hop dropped about 1 request in 20,000.
  - In Phase 5, `GATEWAY_KEEP_ALIVE_SECONDS` must stay above the idle timeout of anything in front of the gateway.
- No migration. The backend has no new setting.

**Oracle action now:** a normal deploy with the same build command (`-f backend/Containerfile`, repository root as context). Anything on Oracle that runs inside the container and names `/app/...` paths must use `/app/backend/...`. Migrations still run from the laptop.
- **Laptop environment:** in `backend/`, run `pip install -e "../packages/shared[coding]" -e ../services/execution -e ".[dev]"` (see `backend/README.md`). The old `pip install -e .` alone no longer brings the libraries.

**When the services are split on Oracle** (Phase 5): build `backend/Containerfile.api` for the core and `services/execution/Containerfile` for the execution service, with the Phase 3 settings.

## Phase 5, step 1: deploy Phases 0–4 as they are (behaviour unchanged)

**Goal:** the code from Phases 0–4 runs on Oracle exactly as the service runs today: one container, `SERVICE_ROLE=all`, no gateway, drain off. Nothing is switched on. The next steps (drain, gateway, second copy, execution process, limits) each get their own blocks, and only after this step is proven.

| | AgentGate | Console |
|---|---|---|
| VM | `130.61.104.247` | `130.61.172.186` |
| Repo | `/opt/audoryn/AgentGate` | `/opt/audoryn-console/Audoryn-Console` |
| Service / container | `audoryn-api.service` / `audoryn-api` | `audoryn-console-api.service` / `audoryn-console-api` |
| Env | `/etc/audoryn/audoryn.env` | `/etc/audoryn-console/audoryn-console.env` |

**What this deploy is:**
- No migration. The two migration files changed only their import lines; the head stays `0023_usage_resets`.
- No new setting and no env edit (so no block D), no new library.
- No frontend change, so no Vercel deploy.
- **AgentGate first, then the Console** (Phase 1e).
- **Changed for anyone running commands inside the container:**
  - the working folder is `/app/backend` (was `/app`);
  - settings come from `audoryn_shared.bootstrap.settings` (was `app.bootstrap.settings`), and the models from `audoryn_shared.infrastructure.database...`. Check snippets in older handoffs that import `app.bootstrap...` or `app.infrastructure.database...` must use the `audoryn_shared` names from now on.

Run each block only after the previous one is checked; stop at any `STOP` or `FAIL`. No block uses `exit`, and nothing prints a secret.

### AgentGate VM (`130.61.104.247`)

**A. Read-only inspection**

```bash
cd /opt/audoryn/AgentGate && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-api.service
df -h / /var/tmp
podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}'
systemctl --user cat audoryn-api.service | grep -vE '^\s*Environment='
podman inspect audoryn-api --format 'workdir={{.Config.WorkingDir}} cmd={{.Config.Cmd}} mounts={{range .Mounts}}{{.Destination}} {{end}}'
```

- **Expected:** branch `main`, a clean tree, `Remote` = the merge SHA, `active`, 4 GB or more free.
- **The unit and the container line:** the start command must not set its own working folder (`-w` / `--workdir`) or its own command, and no volume may be mounted under `/app`. If any of these appear, **stop and send me the output**: the new image's folder is `/app/backend`.

**B. Fast-forward (saves the old SHA for the rollback tag)**

```bash
cd /opt/audoryn/AgentGate && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && echo "$OLD" > "$HOME/agentgate-rollback.sha" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend/Containerfile backend/pyproject.toml backend/migrations packages/shared/pyproject.toml services/execution/pyproject.toml; fi
```

- **Expected:** `After` = the merge SHA. Files listed:
  - `backend/Containerfile` and `backend/pyproject.toml`;
  - `backend/migrations/env.py` and one migration file (import lines only);
  - the two new `pyproject.toml` files.

  No new file under `backend/migrations/versions`.

**C. Tag the rollback image, then rebuild**

```bash
cd /opt/audoryn/AgentGate && podman tag localhost/audoryn-api:latest "localhost/audoryn-api:rollback-$(cut -c1-7 "$HOME/agentgate-rollback.sha")" && podman build -f backend/Containerfile -t localhost/audoryn-api:latest . && podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

- The build command is the same as always (the repository root is the context). The running service is not touched.

**E. Read-only checks with the new image (nothing restarts yet)**

```bash
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python - <<'PY'
import os
from audoryn_shared.bootstrap.settings import settings
from app.main import app

def paths(route):
    nested = getattr(route, "effective_candidates", None)
    return [p for child in nested() for p in paths(child)] if nested else [getattr(route, "path", "")]

found = {p for route in app.routes for p in paths(route)}
print("folder:", os.getcwd())
print("environment:", settings.environment, "| runtime on:", settings.runtime_execution_enabled)
print("service role:", settings.service_role, "| execution call url set:", settings.execution_call_url is not None)
print("drain seconds:", settings.runtime_drain_seconds, "| core CORS on:", settings.cors_enabled)
print("people routes:", any(p.startswith("/api/v1/") for p in found), "| execution routes:", "/internal/v1/runtime/execute" in found)
PY
```

- **Expected:**
  - `0023_usage_resets (head)`;
  - `folder: /app/backend`;
  - `environment: production | runtime on: True`;
  - `service role: all | execution call url set: False`;
  - `drain seconds: 0 | core CORS on: True`;
  - `people routes: True | execution routes: True`.
- Anything else, or an error: **do not restart**; send me the output.

**F. Restart and verify**

```bash
systemctl --user restart audoryn-api.service
sleep 20
systemctl --user is-active audoryn-api.service
curl -sS --max-time 20 http://127.0.0.1:8000/health/ready; echo
curl -sS --max-time 20 https://130.61.104.247/health/ready; echo
[ "$(podman image inspect localhost/audoryn-api:latest --format '{{.Id}}')" = "$(podman inspect --type container audoryn-api --format '{{.Image}}')" ] && echo 'PASS: running the new image' || echo 'FAIL: different image running'
podman logs --since 5m audoryn-api 2>&1 | grep -E "Traceback|ERROR|startup complete"
```

- **Expected:** `active`, both health checks ready, `PASS`, `Application startup complete`, and no `Traceback` or `ERROR`.

### Console VM (`130.61.172.186`), after AgentGate passes

Use blocks A, B, C and F of [the Console's usual deploy](oracle-commands-commercial-plans.md#3-console-vm-13061172186). There is no block D (no env change) and no migration. In block A, expect `agentgate over https: 200`, and ignore that block's key-count lines.

**E. Read-only check with the new image**

```bash
podman run --rm -i --env-file /etc/audoryn-console/audoryn-console.env localhost/audoryn-console-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn-console/audoryn-console.env localhost/audoryn-console-api:latest python -c "from app.application.services import traffic_limits; print('traffic limits module:', traffic_limits.__name__)"
```

- **Expected:** the same revision as before the deploy (no new migration), and `traffic limits module: app.application.services.traffic_limits`.

### Proof that it works (health is not proof)

1. **A real task** at `https://audoryn.vercel.app`: ask "What are the open issues in DIVINEJMG/school". It must finish with an answer.
2. **While it runs, on the AgentGate VM:**

   ```bash
   podman logs --since 10m audoryn-api 2>&1 | grep -E "runtime/execute|outbox/drain|Traceback|ERROR" | tail -20
   ```

   - **Expected:** `POST /internal/v1/runtime/execute` lines with `202`, and no `Traceback` or `ERROR`. A `202` alone does not prove it; the answer in step 1 does.
3. **The Console:** open a workspace page and the platform configuration page; both load as before. Do **not** save `gateway_traffic_limits` yet. Nothing reads it until the gateway is deployed.

### Rollback (only with your confirmation)

- **AgentGate:** run `podman tag "localhost/audoryn-api:rollback-$(cut -c1-7 "$HOME/agentgate-rollback.sha")" localhost/audoryn-api:latest`, then block F. With no migration and no env change, the old image runs exactly as before.
- **Console:** retag its `rollback-<sha>` image the same way, then run its block F.
