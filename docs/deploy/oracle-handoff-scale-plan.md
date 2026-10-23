# Oracle handoff: architecture for production scale

Plan: [docs/plans/architecture-scale-plan.md](../plans/architecture-scale-plan.md). Every phase is proven locally first. Oracle commands (in the A–F format) are written only when a phase reaches Phase 5.

## Phase 0: shutdown drain (built 2026-10-22, branch `feat/scale-phase0-drain`)

**What changed:**
- **New setting `RUNTIME_DRAIN_SECONDS`** (default `0`, at most 900). On shutdown, running steps get this long to finish before they are cancelled; the sweep recovers a cancelled step.
- **No migration, no new dependency, no new endpoint.**
- **A new local-only script, `backend/scripts/restart_drill.py`.** It is never run on Oracle.

**Oracle action now:** none. With the default of `0`, a restart behaves exactly as before. Deploying this branch is a normal deploy.

**Found on Oracle (2026-10-23, Phase 5 step 1, block A):**
- `audoryn-api.service` is a Quadlet unit, generated from `/home/opc/.config/containers/systemd/audoryn-api.container`.
- Its `ExecStop` is `podman rm -v -f -i audoryn-api`, which gives the container podman's default 10 s to stop. So the drain needs `StopTimeout=` and `TimeoutStopSec=` set in that `.container` file, above `RUNTIME_DRAIN_SECONDS`, followed by `systemctl --user daemon-reload`.

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
cd /opt/audoryn/AgentGate && TAG="localhost/audoryn-api:rollback-$(cut -c1-7 "$HOME/agentgate-rollback.sha")-$(date +%Y%m%d)" && if podman image exists "$TAG"; then echo "STOP: $TAG already exists"; else podman tag localhost/audoryn-api:latest "$TAG" && echo "$TAG" > "$HOME/agentgate-rollback.tag" && echo "Rollback image: $TAG" && podman build -f backend/Containerfile -t localhost/audoryn-api:latest . && podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'; fi
```

- **The tag is dated**, because an older `rollback-<sha>` tag may already exist (on 2026-10-23, `rollback-0d34b3a` already named an earlier image). The block never moves an existing tag. It saves the tag's name in `~/agentgate-rollback.tag`.
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
  - `environment: staging | runtime on: True` (Oracle's existing `ENVIRONMENT` value, unchanged by this deploy);
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

This includes a pull (block B). There is no block D (no env change) and no migration. The Console PR also updates Next.js for its web app on Vercel; that needs its own Vercel deploy, with your approval. The Oracle build does not include the web app.

**A. Read-only inspection**

```bash
cd /opt/audoryn-console/Audoryn-Console && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-console-api.service
df -h / /var/tmp
podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}'
curl -sS -o /dev/null -w 'agentgate over https: %{http_code}\n' --max-time 15 https://130.61.104.247/health/ready
```

- **Expected:** branch `main`, a clean tree, `Remote` = the Console merge SHA, `active`, 4 GB or more free, `agentgate over https: 200`.

**B. Fast-forward (saves the old SHA for the rollback tag)**

```bash
cd /opt/audoryn-console/Audoryn-Console && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && echo "$OLD" > "$HOME/console-rollback.sha" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend/Containerfile backend/pyproject.toml backend/migrations; fi
```

- **Expected:** `After` = the merge SHA, and no files listed.

**C. Tag the rollback image (dated, never moved), then rebuild**

```bash
cd /opt/audoryn-console/Audoryn-Console && TAG="localhost/audoryn-console-api:rollback-$(cut -c1-7 "$HOME/console-rollback.sha")-$(date +%Y%m%d)" && if podman image exists "$TAG"; then echo "STOP: $TAG already exists"; else podman tag localhost/audoryn-console-api:latest "$TAG" && echo "$TAG" > "$HOME/console-rollback.tag" && echo "Rollback image: $TAG" && podman build -f backend/Containerfile -t localhost/audoryn-console-api:latest . && podman image inspect localhost/audoryn-console-api:latest --format '{{.Id}} {{.Created}}'; fi
```

**E. Read-only check with the new image**

```bash
podman run --rm -i --env-file /etc/audoryn-console/audoryn-console.env localhost/audoryn-console-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn-console/audoryn-console.env localhost/audoryn-console-api:latest python -c "from app.application.services import traffic_limits; print('traffic limits module:', traffic_limits.__name__)"
```

- **Expected:** the same revision as before the deploy (no new migration), and `traffic limits module: app.application.services.traffic_limits`.

**F. Restart and verify**

```bash
systemctl --user restart audoryn-console-api.service
sleep 20
systemctl --user is-active audoryn-console-api.service
curl -sS --max-time 20 http://127.0.0.1:8001/health/ready; echo
[ "$(podman image inspect localhost/audoryn-console-api:latest --format '{{.Id}}')" = "$(podman inspect --type container audoryn-console-api --format '{{.Image}}')" ] && echo 'PASS: running the new image' || echo 'FAIL: different image running'
podman logs --since 5m audoryn-console-api 2>&1 | grep -E "Traceback|ERROR|startup complete"
```

### Proof that it works (health is not proof)

1. **A real task** at `https://audoryn.vercel.app`: ask "What are the open issues in DIVINEJMG/school". It must finish with an answer.
2. **While it runs, on the AgentGate VM:**

   ```bash
   podman logs --since 10m audoryn-api 2>&1 | grep -E "runtime/execute|outbox/drain|Traceback|ERROR" | tail -20
   ```

   - **Expected:** `POST /internal/v1/runtime/execute` lines with `202`, and no `Traceback` or `ERROR`. A `202` alone does not prove it; the answer in step 1 does.
3. **The Console:** open a workspace page and the platform configuration page; both load as before. Do **not** save `gateway_traffic_limits` yet. Nothing reads it until the gateway is deployed.

### Result (2026-10-23): done on both VMs

| | AgentGate | Console |
|---|---|---|
| Code | `0d34b3a` → `2e16030` (PR #141; also brings PR #140) | `1ee61c0` → `9dfaeb6` (PR #30) |
| Rollback image | `rollback-0d34b3a-20261023` | `rollback-1ee61c0-20261023` |
| New image | `99e370f950d5`, running (`PASS`) | running (`PASS`) |
| Migration | `0023_usage_resets (head)`, unchanged | `0012_announcement_media (head)`, unchanged |
| Health | ready, locally and over HTTPS | ready |
| Logs | clean startup | clean startup |

- **Settings read with the new image:** `environment: staging` (Oracle's existing value), `service role: all`, drain 0, core CORS on, people and execution routes mounted.
- **Proof:** "What are the open issues in DIVINEJMG/school" finished in 15 s with the 6 open issues, read-only. The Console's Settings page lists `gateway_traffic_limits` with `using default`; it is not saved yet, on purpose.
- **Vercel:** the Console's web app still needs its own deploy at `9dfaeb6` for the Next.js 16.4.2 security fix (approval). For AgentGate's web app, deploy at `2e16030` only if production is older than `a1d3cca` (PR #140); this deploy has no other frontend change.

### Rollback (only with your confirmation)

- **AgentGate:** run `podman tag "$(cat "$HOME/agentgate-rollback.tag")" localhost/audoryn-api:latest`, then block F. With no migration and no env change, the old image runs exactly as before.
- **Console:** run `podman tag "$(cat "$HOME/console-rollback.tag")" localhost/audoryn-console-api:latest`, then its block F.

## Phase 5, step 2: the gateway in front of AgentGate

**Goal:** every request goes nginx → `audoryn-gateway` (127.0.0.1:8000) → `audoryn-api` (127.0.0.1:8101). AgentGate itself is unchanged: same image, `SERVICE_ROLE=all`. The gateway routes requests, checks the copies' health, logs one line per request and counts limits (`off` for now). The layout and the reasons for it are in [infrastructure/oracle](../../infrastructure/oracle/README.md).

**What changes on the VM:**
- **A new image,** `localhost/audoryn-gateway:latest` (`services/gateway/Containerfile`).
- **A new env file,** `/etc/audoryn/gateway.env`.
  - Its two secrets, the admin token and the limit secret, are made on the VM and never printed. They exist only for the gateway, so there is no laptop value to copy.
  - Its Redis address is copied from `REDIS_URL` in `audoryn.env`, also without printing.
- **The service files,** from `infrastructure/oracle/quadlet/`:
  - `audoryn-gateway.container` is new;
  - `audoryn-api.container` moves to the host network and port 8101, and trusts forwarded headers only from loopback (the gateway).
- **Nginx is unchanged.** It still calls `127.0.0.1:8000`, which is now the gateway.

**Not changed:** the AgentGate image, `audoryn.env`, the database, QStash's URLs (they still go through nginx), the Console and Vercel.

**Fixed before this step (found while preparing it):**
- **Forwarded headers.** Without `GATEWAY_TRUSTED_PROXIES=127.0.0.1`, the gateway would tell AgentGate `http` and `127.0.0.1` for every request behind nginx. QStash's signature checks, which are made over the `https://` URL, would then fail.
- **Proven locally** with the core's own server settings: `https://130.61.104.247/internal/v1/runtime/execute` and the real caller come through.
- **The admin controls** also judge the real caller, so a request through nginx is never "from this machine".

**Downtime:** about 20 seconds in block F, while `audoryn-api` restarts on its new port and the gateway starts. QStash retries anything refused in that window.

### AgentGate VM (`130.61.104.247`)

**A. Read-only inspection**

```bash
cd /opt/audoryn/AgentGate && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-api.service; systemctl --user is-active audoryn-gateway.service
loginctl show-user opc --property=Linger
ls /home/opc/.config/containers/systemd/
sudo stat -c '%U:%G %a %n' /etc/audoryn/audoryn.env; sudo ls /etc/audoryn/
for k in REDIS_URL GATEWAY_REDIS_PREFIX; do printf '%s: %s line(s)\n' "$k" "$(sudo grep -cE "^\s*$k=" /etc/audoryn/audoryn.env)"; done
ss -ltn | awk 'NR==1 || $4 ~ /:(8000|8101|8102|8201)$/'
```

- **Expected:**
  - branch `main`, a clean tree, `Remote` = the merge SHA;
  - `audoryn-api` `active`, `audoryn-gateway` `inactive`;
  - `Linger=yes` (user services start at boot);
  - only `audoryn-api.container` in the folder, and no `gateway.env` yet;
  - `REDIS_URL: 1 line(s)` and `GATEWAY_REDIS_PREFIX: 0 line(s)`. AgentGate then uses `audoryn:gateway`, the same prefix as the gateway;
  - only 8000 listening.

**B. Fast-forward**

```bash
cd /opt/audoryn/AgentGate && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && echo 'Changed outside the gateway and docs (should be none):' && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend packages services/execution; fi
```

- **Expected:** `After` = the merge SHA, and nothing listed after "Changed outside the gateway and docs". So the AgentGate image does not need rebuilding.

**C. Build the gateway image (nothing running changes)**

```bash
cd /opt/audoryn/AgentGate && podman build -f services/gateway/Containerfile -t localhost/audoryn-gateway:latest . && podman image inspect localhost/audoryn-gateway:latest --format '{{.Id}} {{.Created}}'
```

**D. The gateway's env file, and a backup of the current service file**

```bash
sudo python3 - <<'PY'
from pathlib import Path
import os, secrets, tempfile

source, target = Path('/etc/audoryn/audoryn.env'), Path('/etc/audoryn/gateway.env')
if target.exists():
    raise SystemExit('STOP: /etc/audoryn/gateway.env already exists; no changes made')
redis = [line.split('=', 1)[1].strip() for line in source.read_text().splitlines()
         if line.strip() and not line.lstrip().startswith('#') and '=' in line and line.split('=', 1)[0].strip() == 'REDIS_URL']
if len(redis) != 1 or not redis[0] or redis[0][0] in '"\'':
    raise SystemExit('STOP: REDIS_URL is missing, repeated or quoted in audoryn.env; no changes made')
values = {
    "GATEWAY_CORE_UPSTREAMS": "http://127.0.0.1:8101",
    "GATEWAY_TRUSTED_PROXIES": "127.0.0.1",
    "GATEWAY_ADMIN_TOKEN": secrets.token_urlsafe(32),
    "GATEWAY_REDIS_URL": redis[0],
    "GATEWAY_REDIS_PREFIX": "audoryn:gateway",
    "GATEWAY_LIMIT_SECRET": secrets.token_urlsafe(32),
    "GATEWAY_LIMITS_MODE": "off",
}
st = source.stat()
fd, temp = tempfile.mkstemp(prefix='.gateway.env.', dir=target.parent)
try:
    with os.fdopen(fd, 'w') as f:
        f.write('# The Audoryn Gateway (Phase 5, step 2). Holds secrets: never print or commit.\n')
        f.write(''.join(f'{name}={value}\n' for name, value in values.items()))
        f.flush(); os.fsync(f.fileno())
    os.chown(temp, st.st_uid, st.st_gid); os.chmod(temp, st.st_mode & 0o7777); os.replace(temp, target)
except Exception:
    if os.path.exists(temp):
        os.unlink(temp)
    raise
print('Created:', target, '| owner and mode copied from', source)
print('Keys:', ', '.join(values))
PY
Q="$HOME/.config/containers/systemd" && B="$HOME/audoryn-api.container.backup-$(date +%Y%m%d-%H%M%S)" && cp -a "$Q/audoryn-api.container" "$B" && echo "$B" > "$HOME/audoryn-api.container.backup" && echo "Service file backup: $B"
```

- **Expected:** `Created: /etc/audoryn/gateway.env`, the 7 key names, and the backup path. Nothing running has changed yet.

**E. Read-only checks of the new files, before anything switches**

```bash
QUADLET_UNIT_DIRS=/opt/audoryn/AgentGate/infrastructure/oracle/quadlet /usr/libexec/podman/quadlet -dryrun -user 2>&1 | grep -E "^---|ExecStart=|error|Error|warning"
podman run --rm -i --network host --env-file /etc/audoryn/gateway.env localhost/audoryn-gateway:latest python - <<'PY'
import asyncio
from redis.asyncio import Redis
from audoryn_gateway.settings import GatewaySettings

s = GatewaySettings()
print("core:", [str(u) for u in s.core_upstreams], "| execution:", [str(u) for u in s.execution_upstreams] or "the core's copies")
print("trusted proxies:", s.trusted_proxies, "| limits:", s.limits_mode, "| prefix:", s.redis_prefix)
print("admin token set:", s.admin_token is not None, "| limit secret set:", s.limit_secret is not None)

async def ping():
    redis = Redis.from_url(s.redis_url.get_secret_value())
    print("redis reachable:", await redis.ping())
    await redis.aclose()
asyncio.run(ping())
PY
```

- **Expected:**
  - two `ExecStart=` lines and no errors: the gateway's with `--network host`, and the API's with `--network host` and `--port 8101`;
  - `core: ['http://127.0.0.1:8101/'] | execution: the core's copies`;
  - `trusted proxies: ['127.0.0.1'] | limits: off | prefix: audoryn:gateway`;
  - both secrets set;
  - `redis reachable: True`.
- If you see an error or anything else, **do not run F**; send me the output.

**F. Switch (about 20 seconds of downtime)**

```bash
cd /opt/audoryn/AgentGate && Q="$HOME/.config/containers/systemd" && cp infrastructure/oracle/quadlet/audoryn-gateway.container infrastructure/oracle/quadlet/audoryn-api.container "$Q/" && systemctl --user daemon-reload && systemctl --user restart audoryn-api.service && systemctl --user start audoryn-gateway.service
sleep 20
systemctl --user is-active audoryn-api.service audoryn-gateway.service
ss -ltn | awk 'NR==1 || $4 ~ /:(8000|8101)$/'
curl -sS --max-time 20 http://127.0.0.1:8101/health/ready; echo
curl -sS --max-time 20 http://127.0.0.1:8000/health/gateway; echo
curl -sS --max-time 20 https://130.61.104.247/health/ready; echo
podman logs --since 2m audoryn-gateway 2>&1 | grep -E "Traceback|ERROR|startup complete|rotation"
podman logs --since 2m audoryn-api 2>&1 | grep -E "Traceback|ERROR|startup complete"
```

- **Expected:**
  - `active` twice;
  - 8000 and 8101 listening, both on `127.0.0.1`;
  - the core ready on 8101;
  - the gateway's health shows `core` and `execution` each with `inRotation: 1`;
  - ready over HTTPS (that answer now came through the gateway);
  - both started, with no `Traceback` or `ERROR`.

### Proof that the gateway does its work

1. **Real tasks** at audoryn.vercel.app: the open-issues question, then one that needs approval. Both must finish.
2. **On the VM, while they run:**

   ```bash
   podman logs --since 10m audoryn-gateway 2>&1 | grep -E '"path":"/(internal/v1/runtime|api/v1/conversations)' | tail -8
   podman logs --since 10m audoryn-gateway 2>&1 | grep -cE '"status":5[0-9][0-9]'
   podman logs --since 10m audoryn-api 2>&1 | grep -E "runtime/execute" | tail -3
   ```

   - **Expected:**
     - gateway lines for the task's requests and for QStash's `/internal/v1/runtime/execute` (`202`), each with `"upstream":"core-0"`;
     - `0` server errors;
     - in AgentGate's own log, `runtime/execute` from QStash's address, not from `127.0.0.1`.
3. **Live updates** appear without refreshing the page. The WebSocket now goes through the gateway.
4. **The Console** loads a workspace page. Its signed calls now go through the gateway.

### Rollback (only with your confirmation)

```bash
Q="$HOME/.config/containers/systemd" && systemctl --user stop audoryn-gateway.service && rm -f "$Q/audoryn-gateway.container" && cp -a "$(cat "$HOME/audoryn-api.container.backup")" "$Q/audoryn-api.container" && systemctl --user daemon-reload && systemctl --user restart audoryn-api.service
sleep 20
curl -sS --max-time 20 https://130.61.104.247/health/ready; echo
```

`audoryn-api` is back on 8000 exactly as before. `gateway.env` and the gateway image stay on the VM, unused.

## Fix: a folder permission covers the files inside it (2026-10-23)

**Found on Oracle:** the coding task "add a folder named RED…" stopped with "The file is outside the task's path authority" (work item `002f9486-5c95-43d1-9108-8826b032076d`).
- **What happened:** its plan granted `allowedPaths: ["RED/"]`, and the first file, `RED/SETUP.md`, was refused.
- **The cause:** the check matched paths as whole-path patterns, so `RED/` only ever matched the path `RED/` itself.
- **Age:** this is older than the scale work, which changed only import lines in that code.

**The fix** (`audoryn_shared/execution/providers/native/github/safety.py`):
- A permission ending in `/` is a folder and covers every file inside it. It never covers `REDX/…` or `docs/RED/…`.
- The same rule applies to denied folders.
- Other patterns behave as before.
- `..` paths were already refused, so a folder grant cannot reach outside its folder.

**Also new:** `infrastructure/oracle/gateway_report.py`, a report of what the gateway did, read from its log with the host's `python3`.

**This is a normal backend deploy** with the gateway in front:
- no migration, setting or env change;
- only `audoryn-api` restarts. While it does (about 20 s), the gateway answers 502, and QStash retries.

### AgentGate VM (`130.61.104.247`)

**A. Read-only inspection**

```bash
cd /opt/audoryn/AgentGate && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-api.service audoryn-gateway.service
df -h /
```

- **Expected:** branch `main`, a clean tree, `Remote` = the merge SHA, `active` twice, 4 GB or more free.

**B. Fast-forward**

```bash
cd /opt/audoryn/AgentGate && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && echo "$OLD" > "$HOME/agentgate-rollback.sha" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend packages services infrastructure; fi
```

- **Expected:** `safety.py`, `backend/tests/test_github_expanded.py` and `infrastructure/oracle/gateway_report.py`. No migration and no Containerfile.

**C. Tag the rollback image, then rebuild**

```bash
cd /opt/audoryn/AgentGate && TAG="localhost/audoryn-api:rollback-$(cut -c1-7 "$HOME/agentgate-rollback.sha")-$(date +%Y%m%d)" && if podman image exists "$TAG"; then echo "STOP: $TAG already exists"; else podman tag localhost/audoryn-api:latest "$TAG" && echo "$TAG" > "$HOME/agentgate-rollback.tag" && echo "Rollback image: $TAG" && podman build -f backend/Containerfile -t localhost/audoryn-api:latest . && podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'; fi
```

**E. Read-only check with the new image**

```bash
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python - <<'PY'
from audoryn_shared.execution.providers.native.github.safety import validate_changes
validate_changes([{"path": "RED/SETUP.md", "content": "x"}], {"allowedPaths": ["RED/"]})
try:
    validate_changes([{"path": "REDX/a.py", "content": "x"}], {"allowedPaths": ["RED/"]})
except ValueError:
    print("folder grant: RED/SETUP.md allowed, REDX/a.py refused")
PY
```

**F. Restart AgentGate only (the gateway stays up)**

```bash
systemctl --user restart audoryn-api.service
sleep 20
systemctl --user is-active audoryn-api.service audoryn-gateway.service
curl -sS --max-time 20 http://127.0.0.1:8101/health/ready; echo
curl -sS --max-time 20 https://130.61.104.247/health/ready; echo
[ "$(podman image inspect localhost/audoryn-api:latest --format '{{.Id}}')" = "$(podman inspect --type container audoryn-api --format '{{.Image}}')" ] && echo 'PASS: running the new image' || echo 'FAIL: different image running'
podman logs --since 5m audoryn-api 2>&1 | grep -E "Traceback|ERROR|startup complete"
```

### Proof

1. **Ask again** at audoryn.vercel.app: "In DIVINEJMG/school, add a folder named RED about setup and a small helper script in python, then open a draft PR". It must finish with a draft PR whose files are under `RED/`.
2. **What the gateway did, and the callers AgentGate saw:**

   ```bash
   podman logs --since 60m audoryn-gateway 2>&1 | python3 /opt/audoryn/AgentGate/infrastructure/oracle/gateway_report.py
   podman logs --since 60m audoryn-api 2>&1 | grep -oE 'INFO: +[0-9a-f.:]+:[0-9]+ - "' | awk '{print $2}' | sed -E 's/:[0-9]+$//' | sort | uniq -c | sort -rn | head
   ```

   - **Expected:** 0 `5xx` apart from the restart window, and real caller addresses, not only `127.0.0.1`.

### Rollback (only with your confirmation)

`podman tag "$(cat "$HOME/agentgate-rollback.tag")" localhost/audoryn-api:latest`, then block F.

## Phase 5, step 3: the drain

**Goal:** a restart lets running steps finish (up to 120 s) instead of cutting them off after podman's default 10 s.

**What changes:**
- `RUNTIME_DRAIN_SECONDS=120` in `/etc/audoryn/audoryn.env`.
- `infrastructure/oracle/quadlet/audoryn-api.container`:
  - uvicorn waits at most 20 s for open requests (`--timeout-graceful-shutdown 20`), so a long stream can't eat into the steps' time;
  - podman waits 150 s before killing the container (`StopTimeout=150`), and systemd waits 180 s (`TimeoutStopSec=180`).

**The cost, accepted on 2026-10-23:** until the execution service has its own container (step 4), AgentGate is one container. A restart while a step runs means up to about 2 minutes of `502` for users while it drains. A restart with no step running stays at about 20 s. From step 4 the drain happens in the execution container only.

**Unchanged:** the images, the gateway, nginx, the database.

### AgentGate VM (`130.61.104.247`)

**A. Read-only inspection**

```bash
cd /opt/audoryn/AgentGate && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-api.service audoryn-gateway.service
printf 'RUNTIME_DRAIN_SECONDS: %s line(s)\n' "$(sudo grep -cE '^\s*RUNTIME_DRAIN_SECONDS=' /etc/audoryn/audoryn.env)"
podman inspect audoryn-api --format 'stop timeout now: {{.Config.StopTimeout}}'
```

- **Expected:** branch `main`, a clean tree, `Remote` = the merge SHA, `active` twice, `0 line(s)`, `stop timeout now: 10`.

**B. Fast-forward**

```bash
cd /opt/audoryn/AgentGate && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend packages services infrastructure; fi
```

- **Expected:** only `infrastructure/oracle/quadlet/audoryn-api.container` is listed, so no image rebuild (there is no block C).

**D. The setting, and backups of the env and service files**

```bash
sudo python3 - <<'PY'
from pathlib import Path
from datetime import datetime
import os, shutil, tempfile

VALUES = {"RUNTIME_DRAIN_SECONDS": "120"}
p = Path('/etc/audoryn/audoryn.env')
if not p.is_file():
    raise SystemExit('STOP: env file missing')
lines = p.read_text().splitlines(keepends=True)
for name in VALUES:
    found = [l for l in lines if l.strip() and not l.lstrip().startswith('#') and '=' in l and l.split('=', 1)[0].strip() == name]
    if found:
        raise SystemExit(f'STOP: {name} already present ({len(found)} times); no changes made')
if lines and not lines[-1].endswith('\n'):
    lines[-1] += '\n'
lines += ['# Phase 5 step 3: the drain, added ' + datetime.now().strftime('%Y-%m-%d') + '\n']
lines += [f'{name}={value}\n' for name, value in VALUES.items()]
backup = p.with_name(p.name + '.backup-drain-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
shutil.copy2(p, backup)
fd, temp = tempfile.mkstemp(prefix='.audoryn.env.', dir=p.parent)
try:
    with os.fdopen(fd, 'w') as f:
        f.write(''.join(lines)); f.flush(); os.fsync(f.fileno())
    st = p.stat()
    os.chown(temp, st.st_uid, st.st_gid); os.chmod(temp, st.st_mode & 0o7777); os.replace(temp, p)
except Exception:
    if os.path.exists(temp):
        os.unlink(temp)
    raise
print('Backup:', backup)
print('Added:', ', '.join(VALUES))
PY
Q="$HOME/.config/containers/systemd" && B="$HOME/audoryn-api.container.backup-$(date +%Y%m%d-%H%M%S)" && cp -a "$Q/audoryn-api.container" "$B" && echo "$B" > "$HOME/audoryn-api.container.backup" && echo "Service file backup: $B"
```

- **Expected:** `Backup: /etc/audoryn/audoryn.env.backup-drain-...`, then `Added: RUNTIME_DRAIN_SECONDS`, then the service file backup path.

**E. Read-only checks**

```bash
QUADLET_UNIT_DIRS=/opt/audoryn/AgentGate/infrastructure/oracle/quadlet /usr/libexec/podman/quadlet -dryrun -user 2>&1 | grep -E "error|Error|warning|stop-timeout|timeout-graceful|TimeoutStopSec" | grep -v "^#"
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python -c "from audoryn_shared.bootstrap.settings import settings; print('drain seconds:', settings.runtime_drain_seconds)"
```

- **Expected:**
  - the API's start line includes `--stop-timeout 150` and `--timeout-graceful-shutdown 20`, and `TimeoutStopSec=180` is present, with no errors;
  - `drain seconds: 120`.

**F. Apply (one quick restart: the running container still has the old 10 s timeout)**

```bash
cd /opt/audoryn/AgentGate && cp infrastructure/oracle/quadlet/audoryn-api.container "$HOME/.config/containers/systemd/" && systemctl --user daemon-reload && systemctl --user restart audoryn-api.service
sleep 20
systemctl --user is-active audoryn-api.service audoryn-gateway.service
podman inspect audoryn-api --format 'stop timeout: {{.Config.StopTimeout}}'
podman exec audoryn-api python -c "from audoryn_shared.bootstrap.settings import settings; print('drain seconds:', settings.runtime_drain_seconds)"
curl -sS --max-time 20 https://130.61.104.247/health/ready; echo
podman logs --since 2m audoryn-api 2>&1 | grep -E "Traceback|ERROR|startup complete"
```

- **Expected:** `active` twice, `stop timeout: 150`, `drain seconds: 120`, ready, `startup complete`, and no `Traceback` or `ERROR`.

### Proof: a restart during a running task

1. At audoryn.vercel.app, start a task that takes a while: the RED-folder coding task, or a browser task.
2. While it shows as running, restart and time it:

   ```bash
   time systemctl --user restart audoryn-api.service
   podman logs --since 5m audoryn-api 2>&1 | grep -E "Draining|Cancelled|startup complete"
   ```

   - **Expected:**
     - `Draining 1 running step(s) for up to 120s` before the restart, and no `Cancelled`;
     - the restart takes as long as the step needed (at most about 2.5 minutes);
     - the task finishes normally after the restart.
3. **What users saw meanwhile:**

   ```bash
   podman logs --since 10m audoryn-gateway 2>&1 | python3 /opt/audoryn/AgentGate/infrastructure/oracle/gateway_report.py
   ```

   - The `502`s fall only inside the restart window: the cost described above, until step 4.

### Rollback (only with your confirmation)

```bash
sudo cp -a "$(ls -t /etc/audoryn/audoryn.env.backup-drain-* | head -1)" /etc/audoryn/audoryn.env
cp -a "$(cat "$HOME/audoryn-api.container.backup")" "$HOME/.config/containers/systemd/audoryn-api.container" && systemctl --user daemon-reload && systemctl --user restart audoryn-api.service
sleep 20
curl -sS --max-time 20 https://130.61.104.247/health/ready; echo
```

## Phase 5, step 4: the execution service in its own container

**Goal:**

```
nginx → gateway (8000) ─┬─ core API (8101):          people's routes
                        └─ execution service (8201): task steps, browser, coding, QStash callbacks
```

**What this gives:**
- A restart or crash of one no longer stops the other.
- An API restart takes seconds and never waits for tasks, which ends step 3's two-minute outage.
- Chromium runs only in the execution container.

**What changes on the VM:**
- **Two images** built from the repository: `localhost/audoryn-core:latest` (`backend/Containerfile.api`: no Playwright, no Chromium) and `localhost/audoryn-execution:latest` (`services/execution/Containerfile`). The single-service image `localhost/audoryn-api:latest` stays for rollback.
- **The service files** (`infrastructure/oracle/quadlet/`):
  - `audoryn-api.container` runs the core image with `SERVICE_ROLE=api` and sends due steps to the gateway (`EXECUTION_CALL_URL=http://127.0.0.1:8000`);
  - `audoryn-execution.container` is new: `SERVICE_ROLE=execution`, the drain, and it never runs migrations.
- **`audoryn.env`:** a new internal call key (`INTERNAL_CALL_KEY_ID`, `INTERNAL_CALL_KEY_SECRET`) signs the direct calls from the API to the execution service. It is made on the VM and never printed. It exists only between these containers, so there is no laptop value to copy.
- **`gateway.env`:** `GATEWAY_EXECUTION_UPSTREAMS=http://127.0.0.1:8201`.

**Unchanged:**
- nginx, the database (no migration), QStash's URLs (they reach the execution pool through the gateway), the Console and Vercel;
- the laptop: `SERVICE_ROLE` defaults to `all` there, so local runs stay one process.

**Downtime in F:**
- a few seconds while the gateway restarts with its new upstream;
- then the API restart. **Run F when no task is running.** Otherwise the old single-service container drains first (step 3), up to two minutes.

### AgentGate VM (`130.61.104.247`)

**A. Read-only inspection**

```bash
cd /opt/audoryn/AgentGate && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-api.service audoryn-gateway.service audoryn-execution.service
df -h /
podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}'
for k in INTERNAL_CALL_KEY_ID INTERNAL_CALL_KEY_SECRET SERVICE_ROLE EXECUTION_CALL_URL; do printf '%s: %s line(s)\n' "$k" "$(sudo grep -cE "^\s*$k=" /etc/audoryn/audoryn.env)"; done
sudo grep -E '^\s*DATABASE_MIGRATE_ON_STARTUP=' /etc/audoryn/audoryn.env || echo 'DATABASE_MIGRATE_ON_STARTUP: unset (false)'
printf 'GATEWAY_EXECUTION_UPSTREAMS: %s line(s)\n' "$(sudo grep -cE '^\s*GATEWAY_EXECUTION_UPSTREAMS=' /etc/audoryn/gateway.env)"
ss -ltn | awk 'NR==1 || $4 ~ /:(8000|8101|8201)$/'
```

- **Expected:**
  - branch `main`, a clean tree, `Remote` = the merge SHA;
  - `active`, `active`, `inactive`;
  - **at least 6 GB free.** The two images need about 4.5 GB. If there is less, stop and send me the image list: older rollback images can be removed, with your approval.
  - every key `0 line(s)`;
  - 8201 free.

**B. Fast-forward**

```bash
cd /opt/audoryn/AgentGate && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend packages services infrastructure; fi
```

- **Expected:** only files under `infrastructure/oracle/` are listed. No application code changed.

**C. Build the two images (nothing running changes; about 10 minutes)**

```bash
cd /opt/audoryn/AgentGate && podman build -f backend/Containerfile.api -t localhost/audoryn-core:latest . && podman build -f services/execution/Containerfile -t localhost/audoryn-execution:latest . && podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}' | grep -E 'REPOSITORY|audoryn-(core|execution)'
```

**D. The internal key and the gateway's execution address, with backups**

```bash
sudo python3 - <<'PY'
from pathlib import Path
from datetime import datetime
import base64, os, secrets, shutil, tempfile

def add(path, values, comment):
    p = Path(path)
    lines = p.read_text().splitlines(keepends=True)
    for name in values:
        if any(l.strip() and not l.lstrip().startswith('#') and '=' in l and l.split('=', 1)[0].strip() == name for l in lines):
            raise SystemExit(f'STOP: {name} already in {path}; no changes made')
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    lines += [f'# {comment}, added {datetime.now():%Y-%m-%d}\n'] + [f'{n}={v}\n' for n, v in values.items()]
    backup = p.with_name(p.name + '.backup-execution-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(p, backup)
    fd, temp = tempfile.mkstemp(prefix='.' + p.name + '.', dir=p.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(''.join(lines)); f.flush(); os.fsync(f.fileno())
        st = p.stat()
        os.chown(temp, st.st_uid, st.st_gid); os.chmod(temp, st.st_mode & 0o7777); os.replace(temp, p)
    except Exception:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    print('Backup:', backup, '| added:', ', '.join(values))

for path, name in (('/etc/audoryn/audoryn.env', 'INTERNAL_CALL_KEY_ID'), ('/etc/audoryn/gateway.env', 'GATEWAY_EXECUTION_UPSTREAMS')):
    if any(l.split('=', 1)[0].strip() == name for l in Path(path).read_text().splitlines() if '=' in l and not l.lstrip().startswith('#')):
        raise SystemExit(f'STOP: {name} already in {path}; no changes made')
add('/etc/audoryn/audoryn.env', {
    'INTERNAL_CALL_KEY_ID': 'agentgate-internal-2026-10',
    'INTERNAL_CALL_KEY_SECRET': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='),
}, 'Phase 5 step 4: signed calls from the API to the execution service')
add('/etc/audoryn/gateway.env', {'GATEWAY_EXECUTION_UPSTREAMS': 'http://127.0.0.1:8201'},
    'Phase 5 step 4: the execution service')
PY
Q="$HOME/.config/containers/systemd" && B="$HOME/audoryn-api.container.backup-$(date +%Y%m%d-%H%M%S)" && cp -a "$Q/audoryn-api.container" "$B" && echo "$B" > "$HOME/audoryn-api.container.backup" && echo "Service file backup: $B"
```

- **Expected:** two `Backup:` lines (one for `audoryn.env`, one for `gateway.env`) and the service file backup. Nothing running has changed yet.

**E. Read-only checks with the new images, before anything switches**

```bash
QUADLET_UNIT_DIRS=/opt/audoryn/AgentGate/infrastructure/oracle/quadlet /usr/libexec/podman/quadlet -dryrun -user 2>&1 | grep -E "^ExecStart=|error|Error|warning"
podman run --rm -i --env-file /etc/audoryn/audoryn.env -e SERVICE_ROLE=api -e EXECUTION_CALL_URL=http://127.0.0.1:8000 localhost/audoryn-core:latest python - <<'PY'
import importlib.util
from audoryn_shared.application.internal_calls import load_config
from audoryn_shared.bootstrap.settings import settings
from app.main import app
def walk(route):
    nested = getattr(route, "effective_candidates", None)
    return [p for child in nested() for p in walk(child)] if nested else [getattr(route, "path", "")]
paths = {p for r in app.routes for p in walk(r)}
print("core | role:", settings.service_role, "| key valid:", load_config(settings).enabled,
      "| people routes:", any(p.startswith("/api/v1/") for p in paths),
      "| execution routes:", "/internal/v1/runtime/execute" in paths,
      "| playwright installed:", importlib.util.find_spec("playwright") is not None)
PY
podman run --rm -i --env-file /etc/audoryn/audoryn.env -e SERVICE_ROLE=execution localhost/audoryn-execution:latest python - <<'PY'
import importlib.util, os
from audoryn_shared.application.internal_calls import load_config
from audoryn_shared.bootstrap.settings import settings
from audoryn_execution.service import create_application
def walk(route):
    nested = getattr(route, "effective_candidates", None)
    return [p for child in nested() for p in walk(child)] if nested else [getattr(route, "path", "")]
paths = {p for r in create_application().routes for p in walk(r)}
print("execution | role:", settings.service_role, "| key valid:", load_config(settings).enabled,
      "| execution routes:", "/internal/v1/runtime/execute" in paths,
      "| people routes:", any(p.startswith("/api/v1/") for p in paths),
      "| core installed:", importlib.util.find_spec("app") is not None,
      "| chromium:", any(n.startswith("chromium") for n in os.listdir("/ms-playwright")))
PY
```

- **Expected:**
  - three `ExecStart=` lines (`audoryn-api` with `audoryn-core`, `audoryn-execution` with `--port 8201`, and the gateway), with no errors;
  - `core | role: api | key valid: True | people routes: True | execution routes: False | playwright installed: False`;
  - `execution | role: execution | key valid: True | execution routes: True | people routes: False | core installed: False | chromium: True`.
- If you see anything else, **do not run F**; send me the output.

**F. Switch (run it when no task is running)**

```bash
cd /opt/audoryn/AgentGate && Q="$HOME/.config/containers/systemd" && cp infrastructure/oracle/quadlet/audoryn-execution.container infrastructure/oracle/quadlet/audoryn-api.container "$Q/" && systemctl --user daemon-reload && systemctl --user start audoryn-execution.service && for i in $(seq 1 45); do curl -sf http://127.0.0.1:8201/health/live >/dev/null && echo 'execution service up' && break; sleep 2; done && systemctl --user restart audoryn-gateway.service && systemctl --user restart audoryn-api.service
sleep 25
python3 /opt/audoryn/AgentGate/infrastructure/oracle/check_services.py
```

- **Expected:** `execution service up`, then every line `PASS`, ending in `ALL PASS`. If anything says `FAIL`, send me the output. Rollback is below.

### Proof

1. At audoryn.vercel.app, ask the open-issues question, then a coding task (a new folder). Both must finish, the second with a draft PR.
2. Then, on the VM:

   ```bash
   podman logs --since 15m audoryn-gateway 2>&1 | python3 /opt/audoryn/AgentGate/infrastructure/oracle/gateway_report.py
   ```

   - **Expected:**
     - people's routes answered by `core-0`;
     - `POST /internal/v1/runtime/execute` (`202`) answered by `execution-0`: the API's signed direct calls and QStash's deliveries;
     - no `5xx`.

### Rollback (only with your confirmation)

```bash
Q="$HOME/.config/containers/systemd" && systemctl --user stop audoryn-execution.service && rm -f "$Q/audoryn-execution.container" && cp -a "$(cat "$HOME/audoryn-api.container.backup")" "$Q/audoryn-api.container" && sudo cp -a "$(ls -t /etc/audoryn/gateway.env.backup-execution-* | head -1)" /etc/audoryn/gateway.env && systemctl --user daemon-reload && systemctl --user restart audoryn-gateway.service audoryn-api.service
sleep 25
curl -sS --max-time 20 https://130.61.104.247/health/ready; echo
```

After the rollback, the single-service container (`SERVICE_ROLE=all`) runs everything again, as in step 3. The internal key can stay in `audoryn.env`: with role `all` it is unused.
