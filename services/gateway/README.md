# Audoryn Gateway

The single public entry point in front of AgentGate's services. We build and run it ourselves; there is no third-party gateway. The design and the phases are in [docs/plans/architecture-scale-plan.md](../../docs/plans/architecture-scale-plan.md), section 5.

## What it owns, and what it doesn't

| The gateway owns | The services own |
|---|---|
| The route table and choosing a copy for each request | Authentication, permissions |
| Health checks, draining copies, failover | Plan limits and entitlements |
| Correlation ids (`X-Correlation-ID`) and one access log line per request | QStash signature checks |
| `X-Forwarded-For` / `X-Forwarded-Proto` (set here; taken only from a trusted proxy, see below) | Everything about the request's meaning |
| Traffic limits (1c), CORS and security headers (1d) | |

## Pass-through (Phase 1a)

Every request reaches its service unchanged: HTTP, streamed replies (server-sent events) and WebSockets.

- **The original `Host` is kept.** QStash signs the full callback URL and the core checks it against the URL it sees, so rewriting the host would reject every callback. OAuth callbacks are built from it too.
- **Headers:** hop-by-hop headers stop at the gateway; every other header passes unchanged. That includes repeated ones such as `Set-Cookie`. Requests are built without httpx's default headers.
- **WebSockets:** messages and the close code pass both ways. If a service refuses a connection (bad token), the caller gets the same 403 handshake refusal.
- **Streamed replies** stop the moment the caller disconnects.
- **Failures:** if no copy can be reached, the caller gets `502 {"detail": "The service is unavailable."}`. If a copy was reached but stopped answering, the caller gets `504`.
- **No read timeout,** so nothing that works today is cut off.

## Routing, copies and failover (Phase 1b)

**The route table** is [`audoryn_gateway/routes.py`](audoryn_gateway/routes.py).
- `/internal/v1/runtime|integrations|web-research|outbox/` go to the **execution** pool. Everything else the backend serves goes to the **core** pool.
- A path no service serves gets the gateway's own `404 {"detail": "Not Found"}`, which is the same answer the core gives.
- `backend/tests/test_gateway_route_contract.py` checks that every backend route is in the table, in the right pool. A new route outside it fails CI.
- Until Phase 3 the execution pool holds the core's copies. Setting `GATEWAY_EXECUTION_UPSTREAMS` gives it its own.

**Choosing a copy:** the one with the fewest open requests among those in rotation, with ties taken in turn.

**Health:**
- Every copy's `/health/live` is checked every 5 seconds. Two failures take a copy out of rotation, and two successes bring it back.
- A failed connection takes it out at once.
- The check is liveness, not `/health/ready`. Readiness checks shared dependencies (Postgres, Redis, the AI provider) and can take seconds. When one of those is slow, every copy would fail at once and the gateway would turn all traffic away. The local proof showed exactly this, so readiness stays a deploy check, not a rotation check.

**Failover:**
- A request that never reached a copy (connection refused, or no connection in time) is sent once to another copy, whatever its method, because nothing was sent.
- A GET, HEAD or OPTIONS request without a body is also retried when the connection dropped before a reply. With no other copy in rotation, it goes to the same copy on a fresh connection.
- Idle connections never outlive the other side's patience. The gateway drops its idle connections to the copies after `GATEWAY_UPSTREAM_KEEPALIVE_SECONDS` (3 s), below the copies' 5 s, so it never reuses one a copy is closing. It keeps callers' idle connections for `GATEWAY_KEEP_ALIVE_SECONDS` (75 s), so callers close first. Both were found under load in Phase 4: equal 5-second limits on both sides of a hop dropped about 1 request in 20,000.
- A POST that may have arrived is never sent twice. QStash retries its own deliveries.

**Draining, for planned restarts:**
1. Drain the copy. It gets no new requests.
2. Wait until its open requests reach `0`.
3. Stop it gracefully. Its running steps finish within `RUNTIME_DRAIN_SECONDS` (Phase 0).
4. Start it, and restore it.
5. Wait until it is back in rotation before the next copy.

Open WebSockets stay until the copy closes them on shutdown, and the frontend reconnects through another copy. The gateway refuses (409) to drain the last copy in rotation of a pool, unless the drain says `?last=true`; that pool then answers 502 until the copy is restored.

### Rolling restart (Phase 2)

`python -m audoryn_gateway.rollout` does the five steps above for every copy, one at a time, on the gateway's machine:

```bash
GATEWAY_ADMIN_TOKEN=... python -m audoryn_gateway.rollout --restart "sudo systemctl restart audoryn-core@{port}" --pause-last execution
```

- `--restart` is the command that restarts one copy (a graceful stop, then a start). `{name}`, `{url}` and `{port}` are the copy's.
- `--copies core-1,core-0` sets the order; by default every copy, as the gateway lists them.
- Before Phase 3 the execution pool has one copy (see below), so restarting it pauses that pool. The rollout refuses unless `--pause-last execution` is given. Meanwhile QStash's deliveries get 502 and QStash retries them. The core pool is never emptied: a copy that is the last in rotation of any other pool is refused.
- It stops at the first copy that fails and leaves that copy drained; the others keep serving.

**Why one execution copy:** a browser task's session lives in the memory of the process that opened it, and the task's next step must run there. A process runs a task's next step itself when it can (`RUNTIME_RUN_LOCAL`), but approvals, delays and recovery send it through QStash, and the gateway could hand it to another copy. So the execution pool lists one process.
- **Phase 2** (core copies only): the execution pool lists the copy that runs steps, and every other copy has `RUNTIME_RUN_LOCAL=false`.
- **Phase 3:** the execution pool is the execution process (`SERVICE_ROLE=execution`), and every core copy is `SERVICE_ROLE=api`. The api copies send a due step straight to it through the gateway, signed (`EXECUTION_CALL_URL`), with QStash as the fallback. Restarting it still pauses the pool (`--pause-last execution`); its running steps finish first (`RUNTIME_DRAIN_SECONDS`).

### Admin controls

These are only reachable from the same machine (`127.0.0.1` / `::1`) with `X-Gateway-Admin-Token`. Behind a trusted proxy, a request is judged by the caller the proxy names, so a request that came in through nginx is never "from this machine". They are switched off when `GATEWAY_ADMIN_TOKEN` is unset. Any other caller gets the ordinary 404, so the controls aren't visible from outside.

| Request | Effect |
|---|---|
| `GET /gateway/admin/copies` | Each copy: name (`core-0`, …), address, its pools, healthy, draining, open requests and WebSockets |
| `POST /gateway/admin/copies/{name}/drain` | No new requests to that copy (409 if it is the last one in rotation of a pool; `?last=true` drains it anyway) |
| `POST /gateway/admin/copies/{name}/restore` | Back in rotation once healthy |

`GET /health/gateway` is the gateway's own check. It shows how many copies of each pool are in rotation, not their addresses.

**Shared state:** each gateway runs its own health checks. Drains are shared through Redis when `GATEWAY_REDIS_URL` is set, so a drain made through any gateway copy reaches every copy within one check interval. A drain left behind lapses after 6 hours; the health checks still guard a stopped copy.

## Traffic limits (Phase 1c)

Every request belongs to one route group ([`limits.py`](audoryn_gateway/limits.py)). Each group's policy holds its rate rules and its body cap.

| Group | Requests | Default rules | Body cap |
|---|---|---|---|
| `auth` | POST sign-in, sign-up, GitHub sign-in start and exchange | 30 per 5 min per IP. **Fails closed** | 64 KiB |
| `upload` | POST `…/attachments` | 60/min per session, 120/min per IP | 16 MiB (attachments are at most 10 MiB, about 13.4 MiB as base64) |
| `write` | other POST/PUT/PATCH/DELETE | 600/min per IP, 240/min per session, 1,200/min per organization | 2 MiB |
| `general` | GET/HEAD/OPTIONS | 1,200/min per IP, 600/min per session | 2 MiB |
| `realtime` | WebSocket connects | 60/min per IP | none |
| `internal` | QStash callbacks, the Console channel, provider webhooks (all signed, checked by the service) | no rate rule | 8 MiB |
| none | `/health/*` | never limited | |

The defaults are starting values. `report` mode shows real traffic against them before any is enforced.

**What a rule counts:**
- **per IP:** the caller's address as the gateway sees it. Callers can't set it.
- **per session:** an HMAC fingerprint of the bearer token, or of the WebSocket's `access_token`. The token itself never reaches Redis or the logs.
- **per organization:** the organization id in the path.

Counting uses fixed windows in Redis, one round trip per request for all of its rules.

**Modes** (`GATEWAY_LIMITS_MODE`, or `mode` per group):

| Mode | Effect |
|---|---|
| `off` | Nothing is counted |
| `report` | Counted, and anything over a rule is logged as `gateway.limit` with `"mode":"report"`. Nothing is refused, and the request **doesn't wait for Redis**: it is counted alongside |
| `enforce` | Over a rule gets `429 {"detail": "Too many requests. Try again shortly."}` with `Retry-After`. Over a body cap gets `413`; bodies without `Content-Length` are counted as they stream |

**When Redis is unavailable,** under `enforce` the `auth` group refuses with `503` and every other group lets requests through. An outage of the limiter never takes the product down. The warning is logged at most once every 30 seconds.

**Plan limits are not here.** They stay in the core, which owns that data.

**Changing a group** (`GATEWAY_LIMIT_POLICIES`, JSON): a group named there replaces that group's defaults. For example:

```json
{"general": {"rules": [{"key": "ip", "limit": 2000, "window_seconds": 60}], "max_body_bytes": 2097152, "mode": "enforce"}}
```

**Set in the Console (Phase 1e):**
- The Console's platform-configuration key `gateway_traffic_limits` holds a document in the same shape: `{"mode": ..., "policies": {<group>: {...}}}`, with the version numbered by the Console.
- AgentGate keeps it in Redis at `<GATEWAY_REDIS_PREFIX>:policies`, and every gateway reads it every `GATEWAY_POLICY_REFRESH_SECONDS` (10).
- A group it names replaces that group's setting here, and its `mode` replaces `GATEWAY_LIMITS_MODE`.
- An invalid document is ignored (one warning) and a missing one changes nothing: the last good policies stay.
- `/health/gateway` shows `limits.mode` and `limits.consoleVersion`.

**Latency:** under `enforce` each request waits for one Redis round trip. Run the gateway next to its Redis, in the same region or on the same host. From this laptop Upstash is about 380 ms away, which is fine for `report` but not for `enforce`.

## The edge: CORS and security headers (Phase 1d)

Both are **off by default**, so replies are exactly as before.

- **CORS** (`GATEWAY_CORS_ENABLED`) uses Starlette's own CORS middleware with the core's rules (`edge.CORS_RULES`).
  - `backend/tests/test_gateway_route_contract.py` fails if the two ever differ.
  - **Switch it as a pair:** `GATEWAY_CORS_ENABLED=true` together with the core's `CORS_ENABLED=false`. Browsers refuse a reply that carries the CORS headers twice.
  - `GATEWAY_CORS_ALLOWED_ORIGINS` takes the same value as the core's `CORS_ALLOWED_ORIGINS` (a JSON list or a comma-separated one).
- **Security headers** (`GATEWAY_SECURITY_HEADERS`): `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: strict-origin-when-cross-origin`.
  - Each is added only when the reply doesn't set its own, so the GitHub sign-in pages keep `Referrer-Policy: no-referrer`.
  - `Strict-Transport-Security` is sent only when `GATEWAY_HSTS_SECONDS` is set, once the public address is served over TLS.
- **The only way in:** in production the core and the execution service listen only on the private network, and the gateway is the only public address. That is a deployment step (Phase 5). Locally every process already listens on `127.0.0.1`.

## Settings (`GATEWAY_*` environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `GATEWAY_CORE_UPSTREAMS` | `http://127.0.0.1:8101` | The core's copies, comma-separated |
| `GATEWAY_EXECUTION_UPSTREAMS` | (the core's copies) | The process that runs steps: from Phase 3 the execution process; with Phase 2's core copies, the one copy that runs steps (see Rolling restart) |
| `GATEWAY_HEALTH_PATH` | `/health/live` | What decides rotation |
| `GATEWAY_HEALTH_INTERVAL_SECONDS` / `_TIMEOUT_SECONDS` | `5` / `5` | |
| `GATEWAY_UNHEALTHY_AFTER` / `GATEWAY_HEALTHY_AFTER` | `2` / `2` | Checks in a row to leave or rejoin rotation |
| `GATEWAY_CONNECT_TIMEOUT_SECONDS` | `5` | Connecting to a copy |
| `GATEWAY_WRITE_TIMEOUT_SECONDS` | `60` | Sending a request body |
| `GATEWAY_MAX_CONNECTIONS` | `200` | Pooled connections |
| `GATEWAY_UPSTREAM_KEEPALIVE_SECONDS` | `3` | Idle connections to the copies; keep it below their keep-alive (uvicorn: 5 s) |
| `GATEWAY_KEEP_ALIVE_SECONDS` | `75` | Idle connections from callers; keep it above the idle timeout of whatever sits in front |
| `GATEWAY_TRUSTED_PROXIES` | empty | Addresses of the proxy in front (on Oracle `127.0.0.1`, nginx). From these only, the caller's address is the last `X-Forwarded-For` entry and the scheme is `X-Forwarded-Proto`. Empty: no forwarding header is believed |
| `GATEWAY_ADMIN_TOKEN` | unset (admin off) | Never commit it, never log it |
| `GATEWAY_REDIS_URL` | unset | Limit counters and shared drains. Unset: limits off, drains kept per process |
| `GATEWAY_REDIS_PREFIX` | `audoryn:gateway` | Starts every key, so environments sharing a Redis never mix (local proofs used `audoryn:gateway-sim`) |
| `GATEWAY_REDIS_TIMEOUT_SECONDS` | `0.5` | Per Redis call |
| `GATEWAY_LIMITS_MODE` | `off` | `off`, `report` or `enforce` |
| `GATEWAY_LIMIT_POLICIES` | (defaults) | JSON overrides by group |
| `GATEWAY_LIMIT_SECRET` | random per process | Keys the session fingerprints. Every gateway copy needs the same value; never commit it |
| `GATEWAY_CORS_ENABLED` | `false` | Answer CORS here; pair with the core's `CORS_ENABLED=false` |
| `GATEWAY_CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | Same value as the core's `CORS_ALLOWED_ORIGINS` |
| `GATEWAY_SECURITY_HEADERS` | `false` | Add the security headers above |
| `GATEWAY_HSTS_SECONDS` | `0` (none) | Only once the public address is served over TLS |
| `GATEWAY_LOG_LEVEL` | `INFO` | |

## Running it locally

It runs with the backend's virtual environment, because it uses the same libraries and adds no new dependency.

```text
cd backend && set SERVICE_ROLE=execution && python scripts/run_simulation.py --port 8201      (the execution process)
cd backend && set SERVICE_ROLE=api && set EXECUTION_CALL_URL=http://127.0.0.1:8000 && python scripts/run_simulation.py --port 8101      (api copy 0)
cd backend && set SERVICE_ROLE=api && set EXECUTION_CALL_URL=http://127.0.0.1:8000 && python scripts/run_simulation.py --port 8102      (api copy 1, optional)
cd services/gateway && set GATEWAY_CORE_UPSTREAMS=http://127.0.0.1:8101,http://127.0.0.1:8102
cd services/gateway && set GATEWAY_EXECUTION_UPSTREAMS=http://127.0.0.1:8201
cd services/gateway && python -m audoryn_gateway --port 8000
```

Every backend process needs the same `INTERNAL_CALL_KEY_ID` and `INTERNAL_CALL_KEY_SECRET` (base64url, at least 32 bytes) for the direct calls. Without them, QStash carries every step, as before.

The frontend (5173) and the QStash callbacks in `.env.sim` already point at `localhost:8000`. The rest of the stack is in [docs/testing/ai-simulation.md](../../docs/testing/ai-simulation.md).

The gateway runs uvicorn with `proxy_headers=False`, so callers can't set their own forwarding headers.

## Behind nginx (Oracle)

Nginx terminates TLS and calls the gateway on `127.0.0.1:8000`. With `GATEWAY_TRUSTED_PROXIES=127.0.0.1` the gateway passes on the caller nginx saw and `https`. Without it, the core would rebuild `http://` URLs: QStash signatures (signed over the `https://` URL) and OAuth callbacks would fail, and every caller would share one address for the limits. The image is `services/gateway/Containerfile`; the service files are in [infrastructure/oracle](../../infrastructure/oracle/README.md). Shutting it down waits up to 30 seconds for open requests to finish.

## Tests

```text
cd services/gateway && python -m pytest
```

The tests run two stand-in core copies and the gateway on free ports, over real connections; the rollout tests really stop and start copies on their ports under load. The tests that count limits and share drains need a real Redis in `GATEWAY_TEST_REDIS_URL`; without it they are skipped. Each test run writes only under `audoryn:gateway-test:<random>`, and those keys expire within a minute. CI runs ruff, pyright and pytest in the `gateway` job with its own Redis container. The backend job runs the route contract test.

## Rollback

- **CORS:** set `GATEWAY_CORS_ENABLED=false` and the core's `CORS_ENABLED=true` (its default), as a pair.
- **The gateway itself:** while the core is still reachable directly, run it on the public port again and stop the gateway.
