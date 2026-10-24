# Staging: a second AgentGate, exactly like production

Agreed 2026-10-24. Staging runs the same code, layout, checks and limits as production, with **its own data and accounts**: nothing done on staging can reach production's customers, data or money. Staging is where the deploy pipeline ([pipeline.md](pipeline.md)) sends every commit on `main` before production.

| | Production | Staging |
|---|---|---|
| VM | `130.61.104.247` (Ampere, `aarch64`) | `92.5.169.137` (Ampere, `VM.Standard.A1.Flex`) |
| Database (Neon project `Audoryn-staging-Backup`, `super-mud-48419114`) | branch `production` (`br-snowy-rain-b92m2pri`) | branch `staging` (`br-plain-fog-b9p795xf`), compute `ep-rapid-star-b954jeph`, 0.25–1 CU, made 2026-10-24 22:09 UTC from production |
| TLS | Let's Encrypt certificate for the IP | the same, for `92.5.169.137` |
| Web app (Vercel) | the `production` branch | the `main` branch's preview |

Each phase ends with a check before the next starts.

## Phase 0: Production first

1. **Replace the GitHub App private key.** It was printed in a chat session on 2026-10-24 by a command that listed the env file's key names: the key is spread over several lines in `audoryn.env`, so its lines were printed too. Steps:
   - generate a new private key for the App in GitHub;
   - store it on **one line** with `\n` escapes in `audoryn.env`, as `private_key_pem` expects (`packages/shared/.../github/authentication.py`). The leftover multi-line key lines in the file are removed at the same time;
   - restart the services that use it;
   - delete the old key in GitHub.
2. **Read production's certificate renewal settings** (`/etc/letsencrypt/renewal/130.61.104.247.conf`, no secrets), so staging renews the same way.
3. **Lesson:** a command that lists env key names must print only lines that start with a key name (`^[A-Z_][A-Z0-9_]*=`), never every line.

## Phase 1: Staging's own accounts and data

| Service | Staging gets | Why separate |
|---|---|---|
| **Neon** | branch `staging` of production (done) | migrations and tests never touch production's data |
| **Upstash Redis** | a new database | sessions, live updates (by organization id, and the ids are the same in the copy), limits and the watchdog would cross over |
| **Upstash Blob** | a new bucket | staging's uploads stay out of production's storage |
| **GitHub App** | "Audoryn Staging": production's permissions and events; callback, webhook (`https://92.5.169.137/integration-events/github`) and setup URLs on staging; its own client secret, private key and webhook secret; installed only on test repositories | an App has one webhook URL and one setup URL, and staging must never act on customers' repositories |
| **QStash, AI providers, search, E2B** | shared (production's keys) | each environment calls back to its own address; only usage is shared |
| **Console** | none at first: `CONSOLE_*_ENABLED=false` | production's Console never talks to staging; a staging Console can follow with the same pattern |

## Phase 2: Making the copied data safe

The branch copies everything: customers' conversations, connected integrations and queued work.

1. **A different `INTEGRATION_ENCRYPTION_KEY` on staging.** Copied integration credentials cannot be decrypted there, so staging can never act on a customer's connected accounts. Test integrations are connected again on staging.
2. **A clean-up right after branching, before any staging service starts:**
   - cancel the copied pending runs and outbox work;
   - pause the copied schedules.

   Otherwise staging would replay production's queue, spending AI money and repeating actions. It is one reviewed SQL transaction, run on the `staging` branch only, written from the schema.

Nothing connects to the `staging` branch until this is done.

## Phase 3: The staging VM

One shell session, in the A–F format of the Oracle handoffs.

1. **Base:**
   - podman, nginx, certbot, git;
   - `loginctl enable-linger opc`, so the services keep running;
   - firewalld open for 80 and 443;
   - the SELinux setting that lets nginx proxy to the gateway (`httpd_can_network_connect`).
2. **TLS:** a Let's Encrypt certificate for `92.5.169.137`, renewed as production renews.
3. **nginx:** copied from production. That means `audoryn.conf` (HTTP → HTTPS, the ACME challenge, `/` → `127.0.0.1:8000`, `client_max_body_size 25m`, `proxy_read_timeout 3600s`, the forwarded headers) and the websocket map. Production has two map files (`00-audoryn-websocket-map.conf` and `00-websocket-map.conf`); staging gets the one in use, and the duplicate on production is checked.
4. **Code:** `/opt/audoryn/AgentGate` on the commit production runs.
5. **Env files:** production's, streamed **through the laptop** from VM to VM (`ssh production … | ssh staging …`, never stored on the laptop). Then one script on staging changes these values:

   | Group | Keys | Value on staging |
   |---|---|---|
   | Replaced | `DATABASE_URL`, `REDIS_URL`, `GATEWAY_REDIS_URL`, `QSTASH_*_URL`, `UPSTASH_QSTASH_*_URL`, `QSTASH_FAILURE_CALLBACK_URL`, `GITHUB_*`, `CORS_ALLOWED_ORIGINS`, `GATEWAY_CORS_ALLOWED_ORIGINS`, `UPSTASH_BLOB_*` | staging's own |
   | Generated on the VM | `INTEGRATION_ENCRYPTION_KEY`, `INTERNAL_CALL_KEY_ID` and `_SECRET`, `GATEWAY_ADMIN_TOKEN`, `GATEWAY_LIMIT_SECRET` | new values |
   | Switched off | `CONSOLE_BILLING_ENABLED`, `CONSOLE_ENTITLEMENTS_ENABLED`, `CONSOLE_READS_ENABLED` | `false` |
   | Removed | `LEGACY_DATABASE_URL` | none |
   | Kept | everything else: AI, search, E2B, QStash token and signing keys, feature flags, timeouts, the drain, `ENVIRONMENT` | as production |

6. **Services:** build the three images on the VM, install production's Quadlet files (the gateway, the core template with copies 8101 and 8102, the execution service), and start them.
7. **Check:** `check_services.py` with `AUDORYN_PUBLIC_URL=https://92.5.169.137` gives `ALL PASS`.

## Phase 4: Wiring the pipeline

- **Staging VM:** `/etc/audoryn/deploy.env` with `DEPLOY_ENV=staging`, and a deploy key restricted to `deploy.sh` (the production steps 2.D and 2.E of [pipeline.md](pipeline.md)).
- **GitHub:** the environments `staging` and `production` with their secrets, and a required reviewer on `production`.
- **Vercel:**
  - production deploys from the `production` branch;
  - for the `main` branch's preview, the `VITE_*` URLs point at `https://92.5.169.137` and `wss://92.5.169.137`;
  - that preview's address goes into staging's CORS origins.
- **Then** set the repository variable `DEPLOY_STAGING` to `true`.

## Phase 5: Proving staging matches production

- `deploy.sh status` shows the same commit on both VMs, and `check_services.py` gives `ALL PASS` on both.
- **On the staging web app:**
  - sign in (also with GitHub, through the staging App);
  - run a worker task, and a coding task on a test repository;
  - upload an attachment;
  - watch live updates;
  - trip a limit.
- **One small change goes through the whole pipeline:** merge, staging, promote, production.

## Afterwards

- **Production's `ENVIRONMENT` is `staging` today; it should be `production`.** That turns on the production-only safety checks (no wildcard CORS, no simulated model), so it is its own checked change once staging works.
- **The Console:** the same staging/production split and pipeline.
