# AgentGate on the Oracle VM

The service files for the AgentGate VM (`130.61.104.247`): rootless podman under `opc`, one Quadlet file per service. The files in `quadlet/` are the only copy. The VM installs them from its checkout of `main` (`/opt/audoryn/AgentGate`) into `~/.config/containers/systemd/`, then runs `systemctl --user daemon-reload`. Each change is reviewed in a PR and rolled back by restoring the previous file. Secrets stay in the env files under `/etc/audoryn/` and are never in this folder.

## Layout

Every AgentGate container uses the host network and listens on loopback only. Nginx is the only public listener (80/443), and it calls `127.0.0.1:8000`.

```
internet ─ nginx (TLS) ─ 127.0.0.1:8000 audoryn-gateway ─┬─ 127.0.0.1:8101 audoryn-api@8101 (core-0)
                                                         ├─ 127.0.0.1:8102 audoryn-api@8102 (core-1, from step 5)
                                                         └─ 127.0.0.1:8201 audoryn-execution (from step 4)
```

The core copies are instances of one template, `audoryn-api@.container`, named by their port. A copy is enabled by a symlink next to it in `~/.config/containers/systemd/` (`audoryn-api@8102.container -> audoryn-api@.container`), and its container is `audoryn-api-<port>`.

| Step (Phase 5) | Services | Image |
|---|---|---|
| 1 (done) | `audoryn-api` on 8000, `SERVICE_ROLE=all` | `backend/Containerfile` |
| 2 (done) | `audoryn-gateway` on 8000; `audoryn-api` on 8101, still `all` | + `services/gateway/Containerfile` |
| 3 (done) | the drain: `RUNTIME_DRAIN_SECONDS=120`, stop timeout 150 s (until step 4, a restart during a step blocks users up to ~2 min) | unchanged |
| 4 (done) | `audoryn-execution` on 8201 (steps, browser, drain); `audoryn-api` becomes `api` (no steps, quick restarts); an internal call key in `audoryn.env` | `localhost/audoryn-core` (`backend/Containerfile.api`), `localhost/audoryn-execution` (`services/execution/Containerfile`); `audoryn-api:latest` kept for rollback |
| 5 (done) | the core becomes the template `audoryn-api@.container`: copies on 8101 and 8102, both in `GATEWAY_CORE_UPSTREAMS` | unchanged |
| 6 | limits `report` (then `enforce`, after a few days of real traffic); CORS and security headers at the gateway, the core's `CORS_ENABLED=false` | unchanged |

**Why the host network:**
- With rootless podman's own network (pasta), a container may not see nginx as `127.0.0.1`. The gateway trusts nginx's forwarded headers only from `127.0.0.1`.
- The containers reach each other on loopback, exactly as in the local proofs.

**Capacity:** the VM has 1 CPU and 5.5 GiB of memory.
- The services fit in memory.
- More copies on one CPU give restarts without downtime and isolation, not speed.
- More capacity means copies on more CPUs or machines: a change to the gateway's upstream lists.

## Env files

| File | Read by | Holds |
|---|---|---|
| `/etc/audoryn/audoryn.env` | the core copies and `audoryn-execution` | AgentGate's settings and secrets |
| `/etc/audoryn/gateway.env` | `audoryn-gateway` | `GATEWAY_*`: upstreams, `GATEWAY_TRUSTED_PROXIES=127.0.0.1`, the admin token, Redis (AgentGate's `REDIS_URL`, prefix `audoryn:gateway`), the limit secret, the limits mode, CORS (the core's origins) and security headers |

## Checking the services

`check_services.py` prints PASS or FAIL for each container: running and on its newest image, its port, no tracebacks, its role and settings, readiness, the gateway's pools, and HTTPS end to end. It never prints a secret.

```bash
python3 /opt/audoryn/AgentGate/infrastructure/oracle/check_services.py
```

## What the gateway did

`gateway_report.py` summarises the gateway's log: requests by route, status and copy, with latency, the last errors with their correlation ids, and the limits reached (would refuse under `report`, refused under `enforce`). It needs only the host's `python3`:

```bash
podman logs --since 60m audoryn-gateway 2>&1 | python3 /opt/audoryn/AgentGate/infrastructure/oracle/gateway_report.py
```

## Deploying new core code

After building a new `localhost/audoryn-core:latest`, restart the core copies one at a time. People are served throughout: the next copy restarts only once the gateway has the first back in rotation.

```bash
bash /opt/audoryn/AgentGate/infrastructure/oracle/restart_core.sh
```

The execution service has one copy (a browser session lives in its memory), so `systemctl --user restart audoryn-execution.service` pauses task steps for up to its drain (2 minutes). People's pages keep working, and QStash retries the steps it delivers meanwhile.

The commands for each step are in [the Oracle handoff](../../docs/deploy/oracle-handoff-scale-plan.md).
