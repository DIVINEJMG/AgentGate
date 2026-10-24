# AgentGate architecture for production scale: detailed implementation plan

| | |
|---|---|
| Status | Plan written 2026-10-22. **Nothing built.** Each phase needs approval before work starts. |
| Goal | Host millions of daily users. Deploy and maintain without breaking anyone. Be ready for buyer due diligence. |
| Scope | AgentGate only. The Console keeps working unchanged through its existing channel. |
| Where | Everything is built and proven **locally first**. Oracle comes last (Phase 5). |
| Repo | One repo. New services live under `services/`. No new git repositories. |

---

## 1. Rules

### 1.1 The three rules for this work (from the user)

1. **The system must not break.** Every part keeps working as expected during and after each switch: tasks, browser, coding, chat, approvals, realtime, plans, the Console and CMS reads. In practice this means:
   - every change sits behind a setting whose default is today's behaviour;
   - every new limit runs report-only before it enforces anything;
   - every switch has a one-step rollback;
   - running tasks are never cut off by a restart or deploy.
2. **Code quality and efficiency.** Smart, sharp code with no duplicates.
   - Each concern has one owner and one copy.
   - Services share code by importing one shared package, never by copying.
   - Existing helpers are reused, such as the health checks, the readiness check, the correlation middleware, the leases and the QStash verifier.
3. **Proper documentation.** Every phase ships with its docs (see section 9). This plan's status table is updated at the end of each phase.

### 1.2 Standing rules that also apply

- **We build the API gateway ourselves.** No third-party gateway or edge service.
- **Local first.** No hosting decisions (domain, cluster, budget) until the local proof passes.
- **Nothing hardcoded.** Behaviour is controlled by settings. The gateway's limit policies end up controlled by the Console (step 1e).
- **Ask first** before any new dependency or migration.
  - Production database writes need explicit approval.
  - Migrations run from the laptop, never on Oracle.
- **The end-to-end run of tasks is never disturbed.** Measuring and routing never block or change the work itself.
- **Don't touch the CMS or the signed reads.** Their behaviour stays exactly the same; they are only routed.
- **Commit only when everything is done.** Changes are logged in the handoff, and Oracle commands use the A–F format.
- **Never commit `.env` files or secrets. Never print secrets.** Logs redact tokens, including the WebSocket `access_token` query parameter.
- **Don't touch the dev servers** on ports 3000 and 5173. The local harness uses its own ports (section 7).

---

## 2. Where we are today (traced in the code)

**One process does everything.** `app/main.py` creates the app (`bootstrap/application.py`) and mounts every router from `api/router.py`:
- the public API (`/api/v1`, `/api/v2`), the WebSocket `/ws/v1/organizations/{id}` and the streaming routes;
- the integration webhooks (`/integration-events/{provider}`) and the GitHub callback (`/integrations/github/callback`);
- all `/internal/*` routes: the QStash callbacks and the Console channel.

**Background work runs inside the API process:**
- **Task steps.** `POST /internal/v1/runtime/execute` runs each step as a background task. With `runtime_run_local=True`, a due step skips QStash entirely: `request_runtime_execution` → `run_here(...)` in `runtime/qstash_trigger.py`.
- **Outbox drains.** With `outbox_drain_local=True` they run in-process after each commit.
- **Two loops started by the lifespan** (`bootstrap/lifecycle.py`): the delivery watchdog and preparation recovery.

**A restart cancels running tasks.** On shutdown `stop_runtime_tasks()` cancels them. The sweep recovers them later, but the user sees the interruption.

**One image holds everything.** `backend/Containerfile` installs Chromium, Playwright and the `coding` extra (E2B) into the API image.

**Already in place and reused by this plan:**
- `/health/live` and `/health/ready`, built on `check_readiness()` (`api/router.py`).
- `CorrelationContextMiddleware`, which accepts and returns `X-Correlation-ID`.
- Sessions in Redis (`RedisSessionStore`) and realtime through Redis (`realtime/bus.py`), so several copies can run safely.
- Redis leases and runtime slots (`_claim` in `api/internal/runtime.py`), which stop a step from running twice across processes.
- The durable work items plus the sweep, which recover a step whose process died.
- A separate setting for every QStash target URL.
- `httpx` and `uvicorn[standard]` (which includes `websockets`), so **the gateway needs no new dependency**.

---

## 3. What we are splitting

AgentGate becomes **three deployable parts plus one shared package**, all in this repo.

### 3.1 The Audoryn Gateway (new): `services/gateway/`

The only public entry point. Its design is in section 5.

### 3.2 Core API: answers people

| Area | Code |
|---|---|
| Health | `/health/live`, `/health/ready` |
| Auth and sessions | `api/auth_routes.py`, `api/auth_dependencies.py`, `infrastructure/auth/*` |
| Organizations, members, onboarding | `api/organization_routes.py`, `api/onboarding_routes.py`, `services/onboarding.py` |
| Chat and conversations | `api/conversation_routes.py`, `services/conversations.py`, `conversation_*`, `intent_interpreter.py`, `reply_claims.py` |
| Approvals and governance | `api/governance_routes.py`, `services/approval_conversation.py`, `approval_expiry.py` |
| Workers, templates, jobs, agents | `api/workforce_routes.py`, `template_routes.py`, `jobs_routes.py`, `agent_routes.py`, `worker_*` services |
| Live results | `api/runtime_result_routes.py`, `services/live_results.py` |
| Realtime | `api/realtime_routes.py` (the WebSocket and streams) |
| Plans, limits, resets, billing | `api/billing_routes.py`, `product_ops_routes.py`, `services/entitlements.py`, `usage_resets.py` |
| What's new and CMS reads | `api/news_routes.py`, `services/news.py` |
| Integration setup | `api/integration_foundation_routes.py` (connect, settings, webhook intake `/integration-events/*`), `api/integration_capability_routes.py`, `api/github_routes.py`, `api/github_auth_routes.py` (OAuth, callback) |
| AI operations | `api/ai_operations_routes.py` |
| Console channel | `/internal/console/v1/*` (reads), `/internal/console/entitlements/v1/*`, `/internal/v1/migration/*` |
| AI probe | `/internal/v1/ai/*` |

### 3.3 Execution service: background work

| Area | Code |
|---|---|
| Task step runtime | `runtime/managed.py`, `admission.py`, `worker/`, `worker.py`, `executor/`, `recovery/`, `scheduler.py`, `schedule_wake.py` |
| Execution core | `execution/*` (lifecycle, authorization, provider executor, retry, recovery, provenance, redaction, capability resolver) |
| Browser | `execution/browser/*`, plus **Chromium and Playwright** |
| Coding | `execution/coding/*`, plus **E2B** (the `coding` extra) |
| Connector actions inside tasks | `execution/providers/*`, including `native/` (GitHub, Gmail, Calendar, Drive, Slack, HTTP, web research) |
| Task preparation | `runtime/preparation_worker.py` and its recovery loop, `services/integration_tasks.py`, `integration_events.py` processing, `integration_worker_readiness.py` |
| Web research | `services/web_research.py` |
| Delivery recovery | `runtime/delivery_watchdog.py` |
| QStash callbacks (its only door) | `/internal/v1/runtime/*` (execute, dispatch, sweep, schedule-due, heartbeat, failures, reconcile-browser-origin, storage-smoke), `/internal/v1/integrations/process`, `/internal/v1/web-research/execute`, `/internal/v1/outbox/drain` |

### 3.4 Shared package: one copy, imported by every service

Shared code includes:
- Settings (`bootstrap/settings.py`).
- Database models, migrations and the session factory.
- The domain layer (`domain/*`) and the integrations adapter registry (`app/integrations/`, a 155-line metadata registry).
- The Redis coordinator, leases, sessions store and realtime bus.
- The QStash provider, verifier and triggers (`runtime/qstash_trigger.py`, `outbox_trigger.py`, `outbox_publisher.py`).
- The planner (`runtime/smart_planner.py`, `runtime/planner/`), because the core uses it for live results and the execution side uses it to replan.
- Contracts (`execution/contracts.py`, `ExecutePayload`, the dispatch messages).
- Observability (`observability/*`), audit (`append_audit`), usage metering, and the AI gateway client.

**The outbox:** both services commit outbox rows, so both drain their own commits in-process using the shared publisher, as today. Only the scheduled backstop route `/internal/v1/outbox/drain` lives in the execution service.

### 3.5 What is not split (on purpose)

- **The database:** one Neon Postgres, shared, with one migration chain. Splitting data is a Phase 6 decision, made only from measurements.
- **Chat, approvals, plans and auth stay together in the core.** They change together and share data, so splitting them now would add risk without benefit.

---

## 4. How the services talk

| From → to | How | Today | After |
|---|---|---|---|
| Users, frontend, Console → core | HTTP and WebSocket through the gateway | direct | through the gateway |
| Core → execution, step due now | Today `run_here()` runs in-process with no QStash message. After the split: a **direct signed internal call** to the execution pool | in-process | direct call; on failure, falls back to QStash |
| Core → execution, delayed, recovery or retry | QStash (`QSTASH_RUNTIME_EXECUTE_URL` and the others) | the API's URL | the execution pool's URL |
| Execution → users | Outbox rows → Redis realtime bus → the core's WebSocket | same | same |
| QStash → execution | Signed callbacks, checked with the existing `QStashSignatureVerifier` | the API | the execution pool through the gateway |

**Why a direct call for due steps:** sending every step through QStash would add latency and use up the daily message quota (the code already handles `quota_exhausted`). The direct call:
- reuses the same `execute` handler and the same lease and slot claim, so nothing is duplicated;
- is signed with an internal service key (HMAC with rotation, read from settings, never logged);
- falls back to QStash when the execution pool is busy or down.

---

## 5. The Audoryn Gateway: design

### 5.1 What it owns, and what it doesn't

**It owns:**
- routing and load balancing;
- health checks and drain awareness;
- traffic rate limits and request size and time limits;
- correlation ids, access logs and gateway metrics;
- security headers and CORS (moved from the core, so there is one place for them).

**It doesn't own:**
- authentication decisions or permissions;
- plan limits and entitlements;
- QStash signature checks.

Those stay with the services that own the data. The gateway passes `Authorization` through untouched.

### 5.2 How it is built

- A pure ASGI app (Starlette level, no FastAPI routing overhead), run with `uvicorn`. One pooled `httpx.AsyncClient` per upstream pool, and WebSocket relaying with `websockets`. **No new dependencies.**
- It holds no state. Limit counters and the shared view of upstream health live in Redis, so any number of copies can run.
- Settings come from the shared `Settings`, under a `GATEWAY_` prefix.

### 5.3 Routing table (versioned config, tested in CI)

| Path | Pool |
|---|---|
| `/health/*` | answered by the gateway itself, plus an aggregate of upstream health |
| `/api/v1/*`, `/api/v2/*`, `/ws/v1/*`, `/integration-events/*`, `/integrations/github/callback` | core |
| `/internal/console/*`, `/internal/v1/migration/*`, `/internal/v1/ai/*` | core |
| `/internal/v1/runtime/*`, `/internal/v1/integrations/*`, `/internal/v1/web-research/*`, `/internal/v1/outbox/*` | execution (the core pool until Phase 3) |
| anything else | 404 from the gateway |

### 5.4 Load balancing, health and drain

- **Balancing:** least connections within a pool.
- **Active checks:** `/health/live` every few seconds. A copy that fails N times in a row leaves rotation and comes back after M successes.
  - *Changed in 1b from `/health/ready`:* readiness checks shared dependencies and can take seconds. When one of them is slow, every copy fails it at once and the gateway would refuse all traffic. The live proof showed this.
- **Passive checks:** a failed connection takes a copy out at once, and the active checks bring it back.
- **Draining:** a copy marked draining gets no new requests. The gateway is told before the stop, through its admin control, and the copy is stopped only once its open requests reach zero. Open WebSockets close when the copy shuts down, and the frontend reconnects. The last copy in rotation can't be drained.
- **Retries:** once, to another copy, in two cases:
  - **any method,** when the request never reached the copy (no connection), because nothing was sent;
  - **GET, HEAD and OPTIONS without a body,** when the connection dropped before a reply.
  A POST that may have arrived is never sent twice; QStash does its own retries.
- **Timeouts:** connect, read and total, set per route group. Streaming and WebSocket routes have no total timeout, only an idle timeout.

### 5.5 Limits

- **Policies by route group:** `auth` (login, sign-up, refresh), `general`, `write`, `console`, `internal`, `realtime-connect`.
- **Keys:** IP, a session fingerprint (an HMAC of the bearer token, never stored raw) and the organization id from the path.
- **Algorithm:** a Redis fixed window with an atomic Lua script. One round trip per check.
- **Modes per policy:** `off` → `report` (logs what would be blocked) → `enforce` (429 with `Retry-After`).
- **If Redis is down:** auth fails closed, everything else fails open with a warning metric. A limiter outage never takes the product down.
- **Request limits:** a maximum body size per route group (uploads go straight to storage through presigned URLs, so API bodies are small) and a timeout per route group.
- **QStash callbacks** use the `internal` policy: no per-IP limit, but a body size cap. Their signature is still checked by the execution service.

### 5.6 Headers, CORS and logs

- **CORS:** the gateway answers preflights from `cors_allowed_origins`, exposes `X-Plan-Limit`, and allows the same headers as today. `CORSMiddleware` is removed from the core in the same change (1d), so it never exists twice.
- **Security headers:** HSTS (in production only), `X-Content-Type-Options`, `Referrer-Policy` and `Frame-Options`.
- **Correlation:** an incoming `X-Correlation-ID` is kept if it is valid, otherwise a new one is created and passed upstream. The core already uses it.
- **Access log, one structured line per request:** route group, pool, upstream copy, status, latency, bytes and correlation id. Query strings and auth headers are redacted.
- **Metrics,** using the existing observability names:
  - `audoryn_gateway_request_latency_ms`;
  - `audoryn_gateway_upstream_errors`;
  - `audoryn_gateway_limited` (labelled report or enforce);
  - `audoryn_gateway_upstream_healthy`.

### 5.7 Console control of limits (step 1e)

- The limit policies (numbers and mode) are set in the Console and delivered over the existing Console channel.
- The gateway reads them through Redis with a short cache.
- Safe built-in defaults apply when the Console is unreachable, and the last known policy is kept.
- This follows the "nothing hardcoded, Console controls" rule without the gateway calling the Console on every request.

---

## 6. New settings (all default to today's behaviour)

| Setting | Default | Purpose |
|---|---|---|
| `SERVICE_ROLE` | `all` | `all`, `api` or `execution`: which routers and loops this process mounts |
| `RUNTIME_DRAIN_SECONDS` | `0` | how long running steps get to finish on shutdown (0 means cancel, as today) |
| `EXECUTION_CALL_URL` | unset | where a process that doesn't run steps sends a due step at once (the gateway); unset, QStash carries it |
| `INTERNAL_CALL_KEY_ID` / `_SECRET`, `INTERNAL_CALL_PREVIOUS_KEY_ID` / `_SECRET` | unset (calls off) | the HMAC key for those calls, and the previous one during a rotation; same pattern as the Console channel's keys |
| `GATEWAY_ROUTES_FILE` | built-in | the routing table |
| `GATEWAY_CORE_UPSTREAMS` / `GATEWAY_EXECUTION_UPSTREAMS` | `http://127.0.0.1:8000` | the copies in each pool |
| `GATEWAY_LIMITS_MODE` | `off` | `off`, `report` or `enforce`, overridable per policy |
| `GATEWAY_HEALTH_INTERVAL_SECONDS` and related | sensible defaults | health checks and ejection |

The Phase 0–4 steps don't need a database migration. If step 1e needs a Console table for limit policies, that is a **Console** migration, and I'll ask before it.

---

## 7. Local harness (every phase is proven here first)

- The harness **builds on the existing simulation stack** ([ai-simulation.md](../testing/ai-simulation.md)): the AI simulator on 8788, the local QStash on 8080, a Neon branch, and `scripts/run_simulation.py --port`, which starts AgentGate on any port. No second launcher is written.

  | Process | Port | Role |
  |---|---|---|
  | Gateway | 8000 | the only entry point. This is the address the frontend and `.env.sim`'s QStash callbacks already use, so neither changes |
  | Core copy A | 8101 | `SERVICE_ROLE=api` (or `all` before Phase 3) |
  | Core copy B | 8102 | same as copy A |
  | Execution | 8201 | `SERVICE_ROLE=execution` (Phase 3 onward) |

- Until the gateway exists, the backend stays on 8000 as today.
- The frontend dev server (5173) already talks to `localhost:8000`. The ports 3000 and 5173 processes are never started or stopped by these scripts.
- **Drills the harness runs, scripted and repeatable:**
  - a rolling restart of the core under load;
  - a restart of the execution process while a browser task is running;
  - killing one core copy;
  - Redis down for 30 seconds;
  - the simulator end to end.

---

## 8. Phases

Every phase lists its changes, tests, local proof, rollback and documentation.

### Phase 0: Safety net

**Built (2026-10-22):**
- **`RUNTIME_DRAIN_SECONDS`** (default 0, which is today's behaviour, at most 900): on shutdown, `stop_runtime_tasks()` lets running steps finish for up to that long, then cancels the rest, which the sweep recovers. It logs `Draining N running step(s)` and `Cancelled N running step(s)` as warnings.
  - The change is in `api/internal/runtime.py`, `bootstrap/lifecycle.py` and `bootstrap/settings.py`. No new endpoint, no migration.
  - New deliveries can't reach a stopping process: uvicorn closes its socket before the drain, so QStash retries them later.
  - A step that ends during the drain sends its continuation to QStash, because `serving` is already off. So nothing new starts in a process that is stopping.
- **`backend/scripts/restart_drill.py`:** the local harness for restart proofs. It reuses `run_simulation.py`, restarts the backend gracefully at the first `inference started` log line and reports the result. Documented in [ai-simulation.md](../testing/ai-simulation.md#restart-drill).
- **`CONTRIBUTING.md`:** the expand/contract rule, plus the shutdown rule (the process manager's stop timeout must be longer than the drain).

**Tests:** 3 new tests in `tests/test_runtime_background_planning.py`:
- a drain finishes the quick step and cancels the slow one;
- with no drain, steps are cancelled at once, as before;
- the default is 0.

**Live proof (simulation stack, `s1_open_issues`, drain 300 s):**
- The restart was triggered while steps were in their model call.
- **Two steps finished normally during the drain.**
- One step was waiting on a model answer the recording doesn't have (a leftover task), so it was cancelled at 300 s and left for the sweep.
- Shutdown completed cleanly (`Application shutdown complete`), the backend came back, and the scenario continued.

**What was learned:**
- Readiness can't report `draining` once the stop signal arrives, because uvicorn has already closed the socket. A **draining signal sent before the stop** is only useful when a gateway is choosing between copies, so it moves to **Phase 1b**.
- App info logs from module loggers aren't printed (only uvicorn's logger is), so operational events like the drain log as warnings.

**For Phase 5 (Oracle):** set the container's stop timeout (`podman --stop-timeout`, and the unit's `TimeoutStopSec`) above `RUNTIME_DRAIN_SECONDS`. Otherwise the process manager kills the process mid-drain.

**Rollback:** `RUNTIME_DRAIN_SECONDS=0`.

### Phase 1: The Audoryn Gateway

**1a. Pass-through**
- The gateway proxies every route to one core upstream: HTTP, streaming and WebSocket. It adds correlation ids, access logs and metrics.
- **Proof:** the frontend, the Console and the simulator run through the gateway on port 8000 with the same results as running direct. Latency overhead is measured.

**Built (2026-10-22):** `services/gateway/` (package `audoryn_gateway`); documented in its [README](../../services/gateway/README.md).
- **How it's built:**
  - a raw ASGI app, with one pooled `httpx` client to the core and `websockets` for WebSocket relaying;
  - no new dependency, and the same ruff and pyright settings as the backend;
  - its own CI job (`gateway`: ruff, pyright, pytest).
- **The original `Host` is kept.** QStash signs the callback URL and the core checks it against the URL it sees (`QStashSignatureVerifier`, `url=str(request.url)`), so a rewritten host would reject every callback.
- **Headers:**
  - hop-by-hop headers are dropped and repeated headers such as `Set-Cookie` are kept;
  - `X-Forwarded-For` and `X-Forwarded-Proto` are always set by the gateway, never taken from the caller;
  - requests are built without httpx's default headers, so the core never sees an `Accept-Encoding` the caller didn't send.
- **Streams and WebSockets:**
  - streamed replies stop when the caller leaves;
  - a WebSocket refused by the core is refused to the caller with the same 403;
  - close codes pass through.
- **Failures:** if the core can't be reached, the caller gets 502. If it was reached but stopped answering, the caller gets 504.
- **Logs:** one JSON access line per request, with the path only. Query strings (the WebSocket's `access_token`) and headers are never logged.
- **`/health/gateway`** is its own liveness check. `/health/*` stays the core's.
- **No read timeout in 1a,** so nothing that works today is cut off. Per-route timeouts come in 1b.

**Tests:** 12 tests run over real connections, against a stand-in core and the gateway on free ports:
- method, body and URL passed unchanged, with the caller's host;
- forwarding headers can't be spoofed;
- the correlation id is kept only when it is well formed;
- streaming;
- repeated `Set-Cookie` headers;
- 502 when the core is unreachable;
- WebSocket relay with its close code, and the WebSocket refusal;
- no secrets in the log.

**Live proof (simulation stack: core on 8101, gateway on 8000):**
- **11 of 11 requests are identical direct and through the gateway:**
  - both health checks;
  - three 401 cases;
  - an allowed CORS preflight (200) and a refused one (400);
  - `openapi.json`;
  - an unsigned QStash call (401);
  - a 404;
  - a WebSocket with a bad token (403).
- **QStash's signed callbacks through the gateway were all accepted:** 7 runtime executes (202), integration processing (200) and 2 outbox drains (200). The `s1_open_issues` task moved forward through them.
  - It didn't finish within the 7-minute cap. At the time I blamed old tasks in the sim database holding the runtime slots (the core answered one delivery with its normal 429 "capacity full, retry").
  - **Corrected during 1c:** clearing those tasks was not the whole story. Runs that send steps through QStash callbacks via the gateway sometimes stall inside the backend. See *Open item: intermittent step stall* under 1c.
- **Overhead on `/health/live`, 300 requests:** p50 5.7 → 9.6 ms (+3.9 ms) and p95 8.8 → 11.9 ms. That is inside the 5 ms budget.

**Not covered locally:** the frontend at 5173, because this work never starts or stops the dev servers. It already talks to `localhost:8000`, so it goes through the gateway when both run.

**1b. Routing and balancing**
- The route table, both pools, health checks, ejection, drain awareness and safe retries.
- **Proof:** a rolling restart of two copies shows zero failed requests.

**Built (2026-10-22):** documented in the gateway [README](../../services/gateway/README.md).
- **`routes.py`, the route table.** The execution pool covers `/internal/v1/runtime|integrations|web-research|outbox/`; everything else goes to core; anything unknown gets the core's own 404.
  - `backend/tests/test_gateway_route_contract.py` walks all 311 backend routes and fails CI if one is missing from the table or in the wrong pool.
- **`pools.py`, the copies:**
  - least open requests, with ties taken in turn;
  - liveness checks (`GATEWAY_HEALTH_PATH`, default `/health/live`), with 2 failures to leave rotation and 2 successes to rejoin;
  - a failed connection takes a copy out at once;
  - the execution pool shares the core's copies until `GATEWAY_EXECUTION_UPSTREAMS` is set (Phase 3).
- **Admin controls:** `GET /gateway/admin/copies`, and `POST .../copies/{name}/drain|restore`.
  - Reachable from loopback only, with `GATEWAY_ADMIN_TOKEN`; off when the token is unset.
  - Any other caller gets the ordinary 404.
  - Draining the last copy in rotation gets a 409.
- **Retry rule:** `_may_retry`, described in section 5.4.
- **Pool state lives in each gateway process.** With several gateway copies, it moves to Redis with the limits (1c).

**Tests:** the gateway has 43 tests: the route table, spreading over copies, a failing copy leaving and rejoining, draining and restoring, the last-copy guard, the admin token, failover of a POST that was never sent, a separate execution pool, and the retry rule. The backend has 2 route contract tests.

**Live proof** (two AgentGate copies on 8101 and 8102 behind the gateway, 8 clients sending requests continuously):

| Phase | Requests | Failed |
|---|---|---|
| Steady | 441 | 0 |
| Planned restart of copy 0 (drain → open requests 0 → graceful stop → start → restore → back in rotation) | 1,938 | 0 |
| Planned restart of copy 1, the same way | 1,745 | 0 |
| Copy 1 **killed** abruptly, the 10 seconds after | 1,124 | 0 (out of rotation at once, requests retried on copy 0) |
| After copy 1 came back | 2,136 | 0 |

**What was learned (the first proof run failed):**
- With readiness as the health check, a restarted copy took too long to rejoin, because its readiness kept timing out. When the other copy was then drained, nothing was left, and requests got 502s.
- Three fixes followed: rotation on liveness, the last-copy guard, and "wait until it is back in rotation before the next copy". The last one is now the rule for the Phase 2 rolling restart script.

**1c. Limits in report mode, then enforce**
- **Proof:** report-only logs show realistic headroom. Enforcing returns 429 only in the flood tests. Redis-down behaviour matches 5.5.

**Built (2026-10-22):** `audoryn_gateway/limits.py`; documented in the gateway [README](../../services/gateway/README.md#traffic-limits-phase-1c).
- **Route groups and defaults:**

  | Group | Default rules | Body cap | Notes |
  |---|---|---|---|
  | `auth` | 30 per 5 min per IP | 64 KiB | fails closed |
  | `upload` | 60/min per session, 120/min per IP | 16 MiB | |
  | `write` | 600/min per IP, 240/min per session, 1,200/min per organization | 2 MiB | |
  | `general` | 1,200/min per IP, 600/min per session | 2 MiB | |
  | `realtime` | 60/min per IP | none | WebSocket connects |
  | `internal` | none | 8 MiB | QStash, the Console, provider webhooks |
  | none | never limited | | `/health/*` |

  Each group can be overridden with `GATEWAY_LIMIT_POLICIES`. Phase 1e moves that control into the Console.
- **Counting:** fixed windows in Redis, one Lua round trip per request for all of its rules.
  - Keys: the IP the gateway sees, an HMAC fingerprint of the session token (`GATEWAY_LIMIT_SECRET`; the token never reaches Redis or logs), and the organization in the path.
  - All keys start with `GATEWAY_REDIS_PREFIX`, so environments sharing a Redis never mix.
- **Modes:** `off`, `report` and `enforce`, set globally or per group.
  - **`report` counts in the background, so a request never waits for Redis.** This was measured, below.
  - `enforce` answers 429 with `Retry-After`, and 413 for oversized bodies (checked by `Content-Length`, and while streaming when there is none).
- **If Redis is down:** under `enforce`, `auth` refuses with 503 and everything else passes. The warning is logged at most once every 30 s.
- **Drains are shared through Redis** (`…:drain:<copy>`, lapsing after 6 h), so a drain made through any gateway copy reaches all of them. Health stays each gateway's own view.
- **The gateway's own JSON replies are compact,** like the core's, so its 404 matches the core's byte for byte.

**Tests:** 66 in the gateway. The 5 that count in Redis run against a real Redis: CI has its own container, and the local run used the shared Upstash Redis under `audoryn:gateway-test:*`, with the user's approval.
- route groups;
- fingerprints never containing the token;
- report mode refusing nothing and **not waiting** for an unreachable Redis (the request finished in under 1.5 s while Redis would have timed out at 3 s);
- enforce giving 429 with `Retry-After`;
- each session counted separately;
- body caps only when enforced;
- a drain reaching a second gateway through Redis;
- Redis down: sign-in refused, the rest passing.

**Live proof** (simulation stack, the gateway in front of the core, Redis keys only under `audoryn:gateway-sim:*`):
- **Report mode added no latency:** `/health/live` p50 was 5.5 ms direct and 9.4 ms through the gateway. That is the same +4 ms as 1a, even though Upstash is about 380 ms from this laptop.
- **No `gateway.limit` lines in report mode** across the comparison checks and three full s1 scenarios, so the defaults don't touch normal traffic.
- **Enforce against the real core,** with a test rule of 20 reads per minute per IP and 30 requests sent: 26 got the core's 401 and 4 got 429, because the window rolled over mid-run (each request takes about a second here). Each refusal was logged, and `/health/live` was never limited.
- **10 of the 11 comparison checks were identical.** The remaining difference is `Vary: Origin` on 404s, which comes from the core's CORS layer and moves into the gateway in 1d.

**For production:**
- Under `enforce`, each request waits one Redis round trip, so the gateway must run next to its Redis (same region or host), as the backend's own sessions already need.
- `GATEWAY_LIMIT_SECRET` and the Redis URL go only in the gateway's env file.

**Open item: intermittent step stall (found while proving 1c, not caused by the limits):**

| Run | Steps via QStash callbacks | Result |
|---|---|---|
| Gateway, report mode, 1st | 1 | that step went silent for over 7 min; cancelled |
| Gateway, steps in-process | 0 | completed |
| Gateway, `RUNTIME_RUN_LOCAL=false` (every step via QStash), 1st | 7 | completed in 9.5 min; one policy check took **114 s** |
| Gateway, `RUNTIME_RUN_LOCAL=false`, 2nd, with a lock monitor | (all) | completed normally; slowest policy check 5.5 s |
| Direct, steps in-process | 0 | completed |
| Direct, `RUNTIME_RUN_LOCAL=false` | 6 | completed; slowest policy check 4.3 s |

- **What is known:**
  - During the stalls, the backend's event loop was not blocked: the gateway's health checks never failed. So the step was *waiting*, not frozen.
  - The lock monitor (reading `pg_stat_activity` on the sim database) found backend sessions **idle in a transaction for up to 42 s**, holding the transaction open while awaiting something outside the database (an `integration_notifications` insert, an `actions` update). A transaction held like that makes any other session that needs the same rows wait. That is a likely cause, and it doesn't depend on the gateway.
  - The gateway only changes timing: callbacks arrive later, and the backend's Redis lease calls to the distant Upstash take 3–5 s per callback here.
- **Not yet proven:** whether the stall is caused by the gateway or only exposed by its timing. Three of the gateway runs used the QStash path and two of them stalled; the one direct run with that path didn't.
- **This blocks Phase 3**, where every step arrives this way. The next step is to fix the long-held transactions in the backend (commit before awaiting outside calls) and repeat both paths several times.

**Root cause and fix (2026-10-22):**
- **Where the lock is taken:** before every GitHub action, `DatabaseProviderContextLoader.load()` (`app/execution/provider_executor.py`) reads the connection's credential row **with a row lock** (`FOR UPDATE`), mints the action's scoped token from GitHub, and writes it back.
- **Why it was held so long:** nothing committed until the step ended. For a write, `checkpoint_write_dispatch` committed before dispatch. For a **read**, though, the lock and the database connection were held through every GitHub call of the action.
- **Who waited:** every task on the same connection needs that row, so a concurrent step (a watchdog re-send, a QStash retry, another task) waited for all those network calls. The gateway didn't cause this; its later callbacks only made overlaps likelier. It would grow with traffic.
- **The fix:** the loader's new `release()` commits right after `load()` in `execute_request`, before any provider call.
  - Only the loading's own changes are pending at that point: the step commits just before the action (`managed.py`, the commit before `ActionGateway`), and that is the only caller.
  - The lock now covers only the token work, which is what it exists for. The renewal path, which `load()` also reaches, is covered the same way.
- **Tests:**
  - `tests/test_f30_21_30.py::test_loading_transaction_ends_before_the_provider_is_called` fails without the fix and passes with it;
  - the backend suite: 1,804 pass, and the only failure is the known F56, which depends on this laptop's `.env`.
- **Live (gateway, `RUNTIME_RUN_LOCAL=false`, debug transaction tracer):**
  - a full s1 run completed;
  - in the last run the tracer saw **no transaction left idle over 8 s**;
  - the slowest policy check was 6.5 s.
- **Concurrency, proven:** two s1 tasks ran **at the same time** on the same GitHub connection, every step through the gateway via QStash.
  - The second replay was started after the first task existed, and the first task's planning answers came from the same recording.
  - **Both completed**, 3 minutes apart.
  - **No session waited on a lock** (`pg_stat_activity` monitor), and the slowest policy check was 8.5 s, against the 114 s stall before the fix.
- **The lock itself, measured on the real sim Postgres and the real credential row:** a second task waits **5.18 s** when the first holds the lock across 5 s of provider calls (before the fix), and **1.55 s** when it commits first (the fix; that time is mostly round trips from the laptop to Neon).
- **What the lock still covers:** the remaining idle transactions over 8 s are `SELECT … FOR UPDATE` held while the scoped GitHub token is minted. That is one permission read per repository plus one installation-token request: several seconds from this laptop, a few hundred ms from Oracle.
  - **The lock is needed:** GitHub's user refresh tokens are single-use, so two concurrent renewals would make one fail and wrongly mark the connection *reconnect required*.
- **Proposed refinement (not done; it changes GitHub sign-in handling, so it needs its own approval):**
  - take the row lock only when the user token actually needs renewal (about every 8 h), and scope the token without a lock otherwise;
  - mint the scoped token once per action instead of twice (`prepare()` and `execute_request` both call `load()`).
  - Together these would cut the lock to rare, short windows and halve the GitHub token calls.

**1d. One entry point**
- CORS and security headers move to the gateway, and `CORSMiddleware` is removed from the core.
- In the harness, the core binds to localhost only.
- **Proof:** the browser flows and CORS preflights pass only through the gateway.

**Built (2026-10-22): 1d as switches, every default unchanged.**
- **Core:** a new setting, `CORS_ENABLED` (default `true`).
  - Its CORS rules moved into one constant, `CORS_RULES`, in `bootstrap/application.py`.
  - With the setting off, the core adds no CORS middleware.
- **Gateway:** `edge.py` holds Starlette's `CORSMiddleware` with the same `CORS_RULES` (`GATEWAY_CORS_ENABLED`, default off) and the security headers (`GATEWAY_SECURITY_HEADERS`, default off).
  - Each header is added only when the reply has none of its own.
  - HSTS is sent only with `GATEWAY_HSTS_SECONDS`.
  - The gateway reads `GATEWAY_CORS_ALLOWED_ORIGINS` as a JSON or comma-separated list, like the core reads its own.
  - `starlette` is now listed in the gateway's package. It was already installed with the backend through FastAPI.
- **Why switches and not a removal:**
  - The live frontend reaches the core on Oracle directly. Removing the core's CORS before the gateway serves there would break every browser call.
  - CORS has to be on in exactly one place, because browsers refuse duplicated CORS headers.
  - So the switch is flipped as a pair, in Phase 5.
- **Tests:**
  - **Gateway:** 7 new tests for the edge being off by default, CORS answering like the core (preflight allowed and refused, `Access-Control-Allow-Origin` exactly once, `X-Plan-Limit` exposed, `Vary`), security headers without overriding a reply's own, HSTS only when set, WebSockets through the edge, and both origin formats.
  - **Backend:** the gateway's `CORS_RULES` equal the core's, and the core's CORS is on by default and absent when switched off.
  - **Suites:** gateway 68 + 5 that need Redis; backend 1,806 pass, the one failure being the known F56 (this laptop's `.env`).
- **Live proof:** a core with `CORS_ENABLED=false` behind the gateway with CORS and headers on, compared with a core answering CORS itself, using the real allowed origin from `.env`. **11 of 11 identical**, including the allowed (200) and refused (400) preflights. The `Vary: Origin` difference noted in 1c is gone, and the security headers are present.
- **Binding the core privately** so the gateway is the only way in is a Phase 5 deployment step. Locally every process already listens on 127.0.0.1.

**1e. Console-controlled limits**
- Policies come from the Console through the existing channel, with safe defaults.

**Built (2026-10-22): Console → signed channel → AgentGate → Redis → every gateway.**
- **Console** (branch `feat/gateway-traffic-limits`):
  - **Storage:** the limits are one platform-configuration key, `gateway_traffic_limits` (JSON). It uses the existing configuration screen, its reason, its audit (`platform.configuration.updated`) and `platform.settings.manage`. **No Console migration was needed.** The approved `0013` was dropped for this simpler design.
  - **On save:** `app/application/services/traffic_limits.py` checks the document (route groups, rules, body caps, modes) and numbers its version. Administrators never type one, and a typed version is ignored.
  - **Delivery:** the platform-configuration route sends the document right after the commit. Each reconcile period, the delivery worker compares AgentGate's `trafficLimitsVersion` with the Console's and resends when they differ. When AgentGate holds a *newer* version, it is only reported, not looped on.
- **AgentGate core:**
  - **The channel:** `POST /internal/console/entitlements/v1/traffic-limits` (signed, write scope) stores the document in Redis at `<GATEWAY_REDIS_PREFIX>:policies`. A Lua compare-and-set keeps it newer-only. `GET /status` reports `trafficLimitsVersion`.
  - **No AgentGate migration.**
  - **Light checking only** (`app/application/gateway_limits.py`), so the full policy rules live only in the Console, which checks what is entered, and the gateway, which checks what it reads.
- **Gateway:**
  - `Limiter.follow` reads the key every `GATEWAY_POLICY_REFRESH_SECONDS` (10 s).
  - A group the document names replaces that group's setting, and the document's `mode` replaces `GATEWAY_LIMITS_MODE`.
  - An invalid document is ignored, with one warning per document. A missing key changes nothing. The last good policies always stay.
  - `/health/gateway` shows the mode and the Console version in use.
- **Tests:**
  - **Console** (`tests/test_traffic_limits.py`): checking and numbering, a refused save changing nothing, the audit, and sending and reconcile (failed send, in step, newer, unreachable).
  - **AgentGate channel:** newer-wins and stale, the status version, invalid documents and the wrong scope.
  - **Contract:** the core's document fields equal the gateway's, and the core's Lua script on a real Redis is applied by a real gateway limiter.
  - **Gateway** (real Redis): followed, invalid ignored, missing kept, back to the settings.
  - **Suites:** AgentGate 1,808 pass (the known F56 aside); gateway 69; Console 424 (one failure that also fails without these changes, `test_console_separation…platform.overview.read-503`).
- **Live proof** (the Console's real signed client → the gateway on 8000 → the core → Redis under `audoryn:gateway-sim:*` → the gateway; no Console database touched):

  | Step | Result |
  |---|---|
  | Send v1 (report) | the gateway took v1 within seconds |
  | Reconcile while in step | nothing resent |
  | Send v2 (enforce, general 5/min per IP) | the gateway took v2 |
  | Resend v1 | stale; AgentGate keeps 2 |
  | 8 reads | 5 got the core's 401, 3 got 429 |
  | Redis key deleted | the gateway kept v2 and the reconcile resent it |

- **Live configuration notes:**
  - The core reads the same environment variable name as the gateway, `GATEWAY_REDIS_PREFIX`, and the two must match and share one Redis.
  - Saving the key through the local Console would write the Console's database, so the local proof drove the Console's client directly.

**Tests for Phase 1:**
- the route table and every path group;
- WebSocket relay;
- streaming;
- retry rules (POST is never retried);
- ejection and recovery;
- the limit modes;
- log redaction (no token in the logs).

**Rollback:** point clients back at the core directly. This works until 1d, and after 1d by restoring the core's direct binding and CORS through a setting.

### Phase 2: Several core copies

**Changes:**
- Two core copies behind the gateway, with a rolling restart script: drain, restart, ready, then the next.
- The delivery watchdog and preparation recovery are confirmed safe to run in every copy (they use leases). If either is not, it gets a leader lease from the existing coordinator.

**Tests:** the background loops never act twice when two copies run.

**Proof:** a rolling restart under load shows zero failed requests in the gateway logs.

**Rollback:** one copy.

**Result (done locally, 2026-10-22):**
- **What the audit of every background loop and in-process state found:**

  | What | Two copies at once | Change |
  |---|---|---|
  | Outbox drain | safe: rows taken `FOR UPDATE SKIP LOCKED`, deliveries deduplicated | none |
  | Due schedules | safe: each job locked and re-read | none |
  | Runtime wake-ups | safe: QStash deduplication, and the step lease | none |
  | Coding workspace cleanup | safe: leased per workspace | none |
  | Approval reminders and expiries | **not safe:** no row lock, so two sweeps could post the same reminder or expiry twice | `FOR UPDATE SKIP LOCKED` on the sweep's read |
  | Preparation recovery | **not safe:** each copy knew only its own running preparations, so the other copy started the same one, hit the model-call lease and posted a false "waiting" message | a Redis lease for each preparation, renewed while it runs (`preparation-run:<kind>:<id>`, 90 s) |
  | Browser sessions | **not shareable:** a session lives in the memory of the copy that opened it, and approvals, delays and recovery send the next step through QStash to any execution copy | until Phase 3, one copy runs steps: the gateway's execution pool lists it, and every other copy has `RUNTIME_RUN_LOCAL=false` |
  | Plan cache (`entitlements._cache`) | a plan change reaches the other copy within its 30 s TTL | accepted |
  | Realtime | safe: Redis pub/sub | none |
- **Code:**
  - the lease heartbeat moved to `coordination.keep_leases`, shared by steps and preparations;
  - the approval sweep loads the expired tasks' runs in one query (the quality guard caught the old per-row `get`);
  - the gateway gained `drain?last=true`, each copy's pools in the listing, and `python -m audoryn_gateway.rollout`. The rollout refuses to empty a pool unless that pool is named in `--pause-last` (only `execution`).
- **Tests:**
  - `tests/test_several_copies.py`: one copy prepares while the other's recovery skips it without waking itself; a lost lease cancels the preparation; without Redis a preparation waits for the next pass; the approval sweep skips locked rows;
  - the expiry test now checks that the expired approval's task is woken;
  - gateway `tests/test_rollout.py`: a rolling restart of two real copies under GET and POST load (no failed request), the paused execution pool answering 502, and the refusals.
  - Suites: backend 1,813 pass; gateway 73 pass (6 need Redis and run in CI); ruff and pyright clean; quality guard 0 errors.
- **Live proof** (simulation stack; core-0 runs steps, core-1 has `RUNTIME_RUN_LOCAL=false`; the execution pool is core-0; `s1_open_issues` under 8 workers of steady API load):

  | Run | Restart caught | Failed requests | Task |
  |---|---|---|---|
  | 1 | core-0 while it prepared the task | 0 of 34,539 | completed. core-0 interrupted the preparation at shutdown and kept the checkpoint; core-1's recovery resumed it from the saved draft (no second model call) while core-0 was down. |
  | 2 | core-0 during a step's model call | 0 of 32,026 | completed. `Draining 1 running step(s)`; the step finished in the drain (22.9 s stop), and no step was cancelled. |

- **Open:** preparations are not drained at shutdown (they resume from their checkpoint on any copy, as run 1 shows); the plan cache's 30 s window between copies.

### Phase 3: Split out the execution engine (same image, separate process)

**Changes:**
- `SERVICE_ROLE`:
  - one router list and one lifespan, filtered by role, so nothing is duplicated;
  - `api` mounts sections 3.2 and 3.4 and turns off `runtime_run_local`;
  - `execution` mounts section 3.3 plus health.
- The direct signed call for due steps (section 4), with QStash as the fallback.
- The gateway's execution pool points at port 8201.

**The switch:** move the routes one group at a time: sweep → execute → integrations and web research → outbox backstop.
- Leases stop duplicate runs while both processes can run steps.
- Moving a group back is one config change.

**Tests:**
- each role mounts exactly its routes;
- the same delivery sent to both processes runs once;
- the direct call falls back to QStash when the execution pool is down;
- the signature check rejects unsigned and old keys.

**Proof:** the simulator and the task metrics (duration, success, retries) are at least as good as before. Restarting the execution process during a browser task drains cleanly.

**Rollback:** `SERVICE_ROLE=all` and the URLs back.

**Result (done locally, 2026-10-22):**
- **The roles:**
  - `router_for(role)` in `app/api/router.py` mounts `CORE_ROUTERS`, `EXECUTION_ROUTERS` or both: one list each, nothing duplicated.
  - The lifespan starts the preparation recovery only where preparations run (not `api`). The delivery watchdog runs in every role, since it is armed by that process's own commits.
  - An `api` process never runs a step itself (`_start_now` in `qstash_trigger.py`).
- **The direct call:**
  - `app/application/internal_calls.py` signs a due step with the Console channel's scheme (`console_entitlements.sign` and `verify_request`, now taking a label and a Redis namespace) under its own label `audoryn-internal-v1`, audience and replay records.
  - `/internal/v1/runtime/execute` accepts QStash's signature or this one, and nothing else. It then goes through the same lease and slot claim as before.
  - Refused, busy capacity or unreachable: the step goes through QStash, as before. A delayed step always goes through QStash.
- **Settings:** `SERVICE_ROLE`, `EXECUTION_CALL_URL`, `INTERNAL_CALL_KEY_ID`/`_SECRET` and the previous key (section 6). The names follow the Console channel's key settings rather than the `INTERNAL_SERVICE_KEYS` first planned.
- **The switch, changed from the plan:** all four execution route groups move together, not one group at a time.
  - While the groups are split between two processes, steps could start in both (the sweep and the execute route each start steps), and a browser task would lose its session.
  - The single switch is still one config change each way: `GATEWAY_EXECUTION_UPSTREAMS` and `SERVICE_ROLE`. The leases keep it safe while it happens.
- **Tests** (`tests/test_service_roles.py`):
  - each role mounts exactly its routes, and they match the gateway's pools;
  - a signed call is accepted once (a replay is refused);
  - the previous key still works;
  - unsigned calls, a retired key, an old timestamp, a changed body or path, and calls switched off are all refused;
  - an entitlements signature can't authorise a step;
  - the same step sent to two execution processes runs once (`accepted`, then `busy`);
  - the api process sends a due step straight to execution, gives delayed ones to QStash, and falls back to QStash when execution is down;
  - only roles that run preparations recover them.
  - Backend suite: 1,827 pass. Ruff, pyright and the quality guard are clean; the guard made me split the dispatch function, which had grown to 153 lines.
- **Live proof** (simulation stack): two api copies (8101, 8102), one execution process (8201), the gateway, `s1_open_issues` under 8 workers of steady API load.
  - **The restarts:** the rollout restarted the execution process during a step's model call, then both api copies. The step finished in the drain (`Draining 1 running step(s)`, 36.5 s stop) and none was cancelled.
  - **Requests:** 0 of 53,044 failed.
  - **Where the work ran:** all 6 step records and the preparation were in the execution process; none were in the api copies.
  - **The direct call:** from an api-role process, through the gateway with the real key and Redis replay store, it was answered `direct:accepted`. The step handler found the task finished and did nothing (the 6th record: 3.8 s, no AI call).
  - **Task metrics** against Phase 2's s1 runs:

    | Task | AI calls | QStash messages | DB statements | Steps | Seconds of work |
    |---|---|---|---|---|---|
    | Phase 2, run 1 | 4 | 0 | 340 | 5 | 253 |
    | Phase 2, run 2 | 4 | 2 | 337 | 5 | 245 |
    | Phase 3 | 4 | 2 | 338 | 5 + the no-op direct call | 320 |

    Without the no-op call, Phase 3's work took 316 s against Phase 2's 245–253 s. Most of a step's time here is the model call waiting for the replay's answer, so this figure varies between runs. The step that was mid-call during the drain alone took 72.7 s. No step ran twice: the records are each step's plan and execute phases, the same pattern as Phase 2.
- **Not proven live:** a browser task across a restart of the execution process. No recorded scenario uses the browser. The drain that protects it is the same one proven here and in Phase 0.

### Phase 4: Execution as its own service folder and image

**Changes:**
- Move section 3.3 into `services/execution/` and section 3.4 into one shared package. The core stays in `backend/app`, or `services/core/` if we rename it later.
- Imports change and behaviour does not.
- **Images:**
  - Playwright moves from the base dependencies to a `browser` extra;
  - the core image installs neither `browser` nor `coding`;
  - the execution image installs both.
  - Changing the dependencies is a packaging change, and I'll ask before making it.
- Contract tests on `ExecutePayload` and the dispatch messages, run by both services.
- CI builds and tests each service.

**Proof:** the full suite, the contract tests, the simulator and all harness drills pass. The core image size drops, and the number is recorded.

**Rollback:** the previous images.

**Result (done locally, 2026-10-22).** You chose the full folder move over keeping the code in one package with two images. Done in stages, with the full suite green after each.

- **4a, before anything moved:**
  - **The core no longer loads Playwright.** The browser provider builds its runtime only on first use. Playwright's error classes have a fallback for a process without it. The runtime's interface (`runtime_contract.py`) is shared, and the runtime itself is execution-only.
  - **The composition edges became registration hooks.** Importing `audoryn_execution/service.py` installs three things: the step runner, the full sweep for the watchdog, and the browser runtime. Its `start` and `stop` join the shared lifespan in the same order as before.
  - **The watchdog in an `api` process asks for a sweep through QStash** instead of running one.
  - **The map:** a script built the import graph (absolute and relative imports) and split the modules by rule, with zero cross-boundary imports left.
    - shared code never imports the other two;
    - the core and the execution service never import each other;
    - `app.domain` is shared;
    - Playwright code belongs to the execution service.
- **4b, the move:** a script moved 271 files with `git mv` and rewrote imports, dotted names in strings, and file paths in 370 files.

  | Package | Folder | Modules | Lines |
  |---|---|---|---|
  | Core API (`app`) | `backend/app` | 57 | ~15,200 |
  | Shared (`audoryn_shared`) | `packages/shared/audoryn_shared` | 236 | ~39,600 |
  | Execution (`audoryn_execution`) | `services/execution/audoryn_execution` | 37 | ~10,500 |

- **What the move surfaced and fixed:**
  - Import lines with a trailing `# noqa` lost their comment; restored.
  - The sandbox helper is read as text by shared code, so it moved to shared.
  - The migration runner located `alembic.ini` by counting folders. It now anchors on `backend/`, with a test.
  - **The failure-log formatter kept only frames under `/app/`.** That was a real regression: logs from shared and execution code would have lost their AgentGate lines. It now recognises all three packages, and uses the last match, since the image runs from `/app`.
  - The runtime benchmark patched only modules named `app.*`.
  - About 30 CI verify scripts read `backend/app` by path. `scripts/code_paths.py` now finds a file in its package and scans all three, so no check got weaker.
  - **A dead console script:** `audoryn-worker` pointed at an empty package that hid `runtime/worker.py`. It now points at the real module.
  - The quality guard now follows moved files and ignores lines that only change an import path. It still reports 0 errors.
- **Not moved (yet):**
  - The GitHub provider stays shared. Its setup is used by the core's GitHub sign-in, and it imports its action modules eagerly. Splitting it would let about 4,400 lines move to the execution service.
  - The E2B adapter stays shared, because the core's "cancel coding" route uses it. So the core image keeps the small E2B SDK (the `coding` extra), not "neither" as planned.
- **4c, packaging, contracts and CI:**
  - **Dependencies:** `packages/shared/pyproject.toml` holds the libraries, and Playwright belongs to the execution service. No service names the shared package as a dependency, so pip never looks it up on an index.
  - **Images:** `backend/Containerfile` is the single service, as before; its working folder is `/app/backend`. `backend/Containerfile.api` is the core alone, and `services/execution/Containerfile` the execution service alone. None was built: no Docker, as you asked.
  - **The contract:** `contracts/dispatch-messages.json` pins the messages to the execution service.
    - The senders' side (`backend/tests/test_message_contracts.py`) captures the real messages and statically checks every `enqueue` of those topics in all three packages.
    - The receivers' side (`services/execution/tests`) checks the route models accept exactly the contract.
  - **CI:**
    - `core-alone` installs the core without Playwright or the execution service, starts it and runs its contract test;
    - `execution-alone` installs the execution service without the core and runs its tests;
    - `backend` runs everything.
    - Both standalone jobs were run locally in clean virtual environments.
- **The size**, measured as installed libraries on this laptop (without Docker; both include ~33 MB of test tools):

  | Install | Libraries | Chromium |
  |---|---|---|
  | Core API alone | 209 MB | none |
  | Execution service | 283 MB (Playwright 109 MB) | 707 MB |
  | Single service (before) | ~318 MB | 707 MB |

  The core drops from ~1,025 MB to ~209 MB. Linux image sizes, with Chromium's system libraries, will be recorded at the Phase 5 build.
- **The gateway, found by the proof:**
  - The first three runs each dropped one request (2 dropped connections and 1 `502`).
  - The cause is equal 5-second keep-alive limits on both sides of a hop: a connection reused just as the other side closed it. A safe request then had no other copy to retry on during a drain.
  - The gateway now drops idle connections to the copies after 3 s, below their 5 s, and keeps callers' connections for 75 s. A safe request retries on the same copy when it is the only one in rotation.
  - A test reproduces the 502 with the old rule and passes with the fix.
  - The problem dates from Phase 1b; this phase didn't cause it.
- **Live proof** (as in Phase 3: two api copies, the execution service, the gateway, `s1_open_issues` under steady load; the execution service restarted during a step's model call, then both api copies):
  - **two runs after the gateway fix:** 0 failed of 27,042, and 0 failed of 38,833;
  - **steps:** every step ran in the execution service and none was cancelled; the step finished in the drain (22–23 s stops);
  - **the direct call** was accepted, and none fell back to QStash;
  - **cost:** 4 AI calls, 2 QStash messages and 339–343 statements per task, the same as Phases 2 and 3.
  - One run also had a DNS failure reaching Neon (`gaierror`). The step kept its checkpoint, was recovered, and the task completed.
- **Suites:**
  - backend: 1,830 pass;
  - gateway: 75 pass (6 run with Redis in CI);
  - execution service alone: 6 pass;
  - pyright clean across all three packages; ruff, the format checks, the quality guard and every CI verify script pass.

### Phase 5: Production rollout (only after the local proof)

- Oracle, step by step, each step with an A–F handoff and rollback:
  1. the drain (Phase 0);
  2. the gateway in pass-through;
  3. a second core copy;
  4. the execution process;
  5. limits in report mode, then enforce.
- The old single service stays ready until each step is proven.
- **The layout on the VM** ([infrastructure/oracle](../../infrastructure/oracle/README.md)):
  - every container uses the host network and listens on loopback only; nginx stays the only public listener and calls the gateway on `127.0.0.1:8000`;
  - the Quadlet files live in the repository.
- **Found while preparing step 2 (fixed before it):** behind nginx, the gateway would have told the core `http` and `127.0.0.1` for every request. That would break QStash's signature checks (made over the `https://` URL), the OAuth callbacks and the per-IP limits. `GATEWAY_TRUSTED_PROXIES` takes the caller and the scheme from nginx only.
- **The VM has 1 CPU.** More copies there buy restarts without downtime and isolation, not speed. Capacity comes from copies on more CPUs or machines, through the gateway's upstream lists (Phase 6).
- TLS for the public entry point, and whether to move from the VM to managed containers, are decided here from measured load.

### Phase 6: By the numbers

Further splits, a dedicated queue or read replicas, only when metrics show the need.

---

## 9. Documentation delivered

| Doc | When |
|---|---|
| This plan: status table and lessons | the end of every phase |
| `services/gateway/README.md`: what it owns, route table, limits, settings, how to run, drain and roll back | Phase 1 |
| `services/execution/README.md`: what it owns, roles, drain, direct call, settings | Phases 3 and 4 |
| `docs/architecture/service-boundaries.md`: section 3 kept up to date as the source of truth | Phase 1, updated in each phase |
| `CONTRIBUTING.md`: the expand/contract rule; one owner for each concern | Phase 0 |
| A local harness guide and drills | Phase 0 |
| Oracle handoff (A–F) for each production step | Phase 5 |

---

## 10. Risks and how they are handled

- **A gateway bug blocks traffic:** each step can be turned off; limits run report-only first; the route table is tested; the direct path stays until 1d.
- **The gateway adds latency or becomes a bottleneck:** it is pure ASGI with pooled connections and holds no state, so copies scale out. Its overhead is measured from 1a, with a budget of 5 ms or less at p50.
- **WebSocket or streaming breaks:** dedicated relay tests, the realtime flow in the simulator, and graceful reconnects on drain.
- **A step runs twice during the split:** the existing leases prevent it, and an explicit test covers it.
- **Steps get slower or hit the QStash quota after the split:** the direct signed call keeps due steps immediate, with QStash only as the fallback and for delays.
- **A deploy cuts off tasks:** the drain, plus the gateway skipping copies that are draining.
- **A schema mismatch between copies during a rolling deploy:** the expand/contract rule.
- **Duplicate code creeps in:** one shared package, review against section 3, and the "one owner" rule.

## 11. Out of scope for now

- A third-party gateway or edge service.
- Splitting the database.
- A new queue product.
- Kubernetes.
- Domain and TLS decisions (Phase 5).

## 12. Status

| Phase | State |
|-------|-------|
| 0 Safety net + local harness | **done locally** (2026-10-22); Oracle rollout in Phase 5 |
| 1a Gateway pass-through | **done locally** (2026-10-22) |
| 1b Routing + balancing | **done locally** (2026-10-22) |
| 1c Limits (report → enforce) | **done locally** (2026-10-22); step-stall root cause fixed (credential lock) and proven under concurrency; token-lock refinement proposed, see 1c |
| 1d One entry point | **done locally as switches** (2026-10-22); flipped as a pair in Phase 5 |
| 1e Console-controlled limits | **done locally** (2026-10-22); Console branch `feat/gateway-traffic-limits` |
| 2 Core copies | **done locally** (2026-10-22); one copy runs steps until Phase 3 (browser sessions) |
| 3 Execution process | **done locally** (2026-10-22); all four route groups switch together (see Phase 3) |
| 4 Execution service + images | **done locally** (2026-10-22); three packages, three Containerfiles (not built: no Docker), gateway keep-alive fix |
| 5 Production rollout | **steps 1–5 done** (2026-10-23): Phases 0–4 on Oracle, the gateway in front of AgentGate behind nginx (trusted-proxy fix first), the drain, the execution service in its own container, and two core copies (one template, deploys without downtime). Step 6 (limits in report, CORS and security headers at the gateway) ready; enforce after a few days of report. Staging comes after. Commands and results in the [Oracle handoff](../deploy/oracle-handoff-scale-plan.md) |
| 6 By the numbers | not started |
