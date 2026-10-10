# Console reads: checkpoints

Plan: [../console-admin-reads-plan.md](../console-admin-reads-plan.md). Nothing here is
committed, pushed, deployed or configured with real keys.

## How checks were run (10 October 2026)

- Backend environment: `backend/.venv` created with `pip install -e ".[dev]"`, which is what CI uses,
  but on **Python 3.14.7**, the only 3.13+ interpreter on this machine. CI uses 3.13.
- `backend/.env` points at live services (Neon, Upstash). Tests and scripts were therefore
  run with the **working directory outside `backend/`**, so settings never load that file,
  matching CI, which has no `.env`. The command shape:
  `backend/.venv/Scripts/python.exe -m pytest -c backend/pyproject.toml --rootdir backend -p no:cacheprovider backend/tests`.
- No database, Redis or network service was contacted by any check.

## Phase 0: baseline before any change

| Check | Result |
|---|---|
| `ruff check app tests migrations` | 2 errors, both I001 import order, in `app/api/v2/router.py` and `app/bootstrap/application.py` (existing uncommitted changes) |
| `pyright` (venv interpreter) | 205 errors, existing: mostly `reportArgumentType` in tests (`test_github_login.py` 49, `test_f30_*` …) |
| `pytest` (991 tests) | **984 passed, 7 failed** (listed below) |
| `audit_f28_endpoints.py --check --require-complete` | Passed |
| `verify_no_legacy_platform`, `verify_f28_architecture`, `_baseline`, `_persistence`, `_redis_boundaries`, `_action_gateway`, `_api_parity`, `_contracts`, `_security_invariants`, `_21_26` | Passed |
| `verify_f27_boundaries`, `verify_f28_11_20` | Failed: untracked, unused `frontend/src/components/GitHubConnection.tsx` mentions `INTEGRATION_ENCRYPTION_KEY` |
| Frontend `npm run typecheck` / `npm test` / `npm run build` | Clean / 87 of 87 / passed |

Existing pytest failures, unrelated to this work and left alone:

1. `test_f31_31_40::test_conversation_failure_semantics_distinguish_provider_and_auth`: message wording changed.
2. `test_f31_31_40::test_normal_worker_ux_is_conversational_and_manual_setup_remains`: reads
   `frontend/src/components/WorkerExperienceProfile.tsx`, removed by the workspace redesign.
3. `test_integration_foundation::test_current_adapters_do_not_simulate_oauth_or_events`: GitHub provider refactor in progress.
4. and 5. `test_planner_stream_activity::…[nvidia]`, `…[openrouter]`; 6. `…::test_progress_outage_…`: the test double lacks `reserve_ai_budget`.
7. `test_qstash_free_plan::test_work_queue_exposes_dispatch_health_…`: expects the text
   "Runtime dispatch delayed." in the jobs workspace, which the workspace redesign removed.

## Phase 1: contract files

- `contract.v1.json`: envelope, error body and the `data` schema for `health`, `snapshot`,
  `overview`, `users`, `user`, `organizations` and `organization`, with the limits and forbidden keys.
- `examples/`: 9 examples (one per operation, a not-found detail, an error body).
- `signing-vector.json`: **test-only** key, target with `%40`, `%20` and `%2B`, and the expected signature.
  It was verified with Console's own `verify_read_request` (run read-only from the Console
  repo, which was not changed).
- `backend/tests/contract/test_console_reads_contract.py`: **20 passed**.

## Phase 2: receiver foundation

New files:

- `backend/app/application/console_reads/signing.py`: verifier (port of Console's scheme)
- `backend/app/application/console_reads/config.py`: settings → keys; an invalid config turns the feature off and never stops startup
- `backend/app/application/console_reads/guard.py`: switch, signature, replay (Redis
  `console-reads:replay:*`, fails closed), rate limit (`console-reads:rate:*`), parameter
  check, envelope, error mapping and logging that contains no values
- `backend/app/api/internal/console_reads.py`: the router; only `GET /internal/console/v1/health`
- `backend/tests/test_console_reads_foundation.py`

Edits to existing files (additive only):

- `backend/app/api/router.py`: one import and one `include_router`
- `backend/app/bootstrap/settings.py`: 11 optional `console_reads_*` settings, off by default
- `.env.example`: placeholders with empty secrets

Results:

| Check | Result |
|---|---|
| `test_console_reads_foundation.py` | **47 passed**. Each 401 case asserts its exact internal reason (unknown key, wrong audience, wrong scope, expired either way, malformed header, bad signature for wrong secret, added query and changed path). |
| New and changed files: ruff / pyright | Clean / 0 errors |
| Whole-repo ruff | Same 2 existing errors only |
| Architecture scripts and endpoint audit | Same results as baseline |
| Whole-repo pyright | 205 errors: identical to baseline, none in new files |
| Full pytest (1,058 tests) | **1,051 passed, 7 failed**: the same 7 as baseline; 67 new tests all pass |

With default settings, `/internal/console/v1/health` answers 404 and nothing else in the app changes.

Notes for later phases:

- A path under `/internal/console/v1/` that has no declared route gets FastAPI's default
  404 body (`{"detail":"Not Found"}`), not the contract error body. Console treats every 404
  as "unconfigured", so this is harmless. It is noted here rather than adding a catch-all route.
- `verify_no_legacy_platform.py` scans `frontend/node_modules` and `backend/.venv` locally,
  so it takes several minutes here. It passed.

## Stale tests updated (10 October 2026, approved by the owner)

The 7 existing failures were stale tests, not product bugs. Each test was updated to check
the current, intended behaviour:

| Test | Why it was stale | Change |
|---|---|---|
| `test_f31_31_40::test_normal_worker_ux_…` | Worker profile moved to `workspace/pages/WorkerProfilePage.tsx` in the redesign | Checks the same behaviour there: Message link to the conversation, Right now, Next scheduled work, Recent results, Settings tab |
| `test_qstash_free_plan::test_work_queue_exposes_dispatch_health_…` | Redesign reworded the notice | Expects "Starting work is delayed." and "Queued work stays safe" |
| `test_f31_31_40::test_conversation_failure_semantics_…` | Message reworded in the uncommitted `conversations.py` change | Expects "could not pass response validation" and "Your message is saved" |
| `test_integration_foundation::test_current_adapters_…` | The default GitHub adapter is the legacy one; the expanded adapter only runs behind `GITHUB_EXPANDED_ENABLED` | Only the expanded adapter may have OAuth and event hooks, and it is checked directly |
| `test_planner_stream_activity` (3 tests) | Fake coordinator predates `reserve_ai_budget` / `settle_ai_budget` | Fake now admits the budget (`-1`) and accepts settlement |

Full pytest afterwards: **1,058 passed, 0 failed**. Ruff on the changed tests is clean, and whole-repo pyright is unchanged.

## Phases 3–5: overview, users, organisations, operations, queues (10 October 2026)

New files:

- `backend/app/application/console_reads/database.py`: read-only runner (PostgreSQL:
  `SET TRANSACTION READ ONLY` plus `SET LOCAL statement_timeout`, always rolled back;
  SQLSTATE 57014 → 504), parameter parsing and LIKE escaping
- `backend/app/application/console_reads/visibility.py`: snapshot, overview, users, user,
  organizations, organization
- `backend/app/application/console_reads/operations.py`: operations and queues
- `backend/tests/test_console_reads_data.py`

Changed: `api/internal/console_reads.py` (8 new routes), `guard.py` (replaceable query
runner for tests), contract (`operations`, `queues` shapes plus 2 examples), contract test.

How the data tests work: AgentGate's own model tables are created in SQLite with their real
constraints, then seeded with a scenario that has known answers. The real query functions run
through signed HTTP requests. Every response is validated against the contract and Console's
limits, and every statement is also compiled for PostgreSQL. Secrets are planted in excluded
columns (password hash, integration config, failure payload, URL password and token) and the
tests check they never appear in any response.

| Check | Result |
|---|---|
| `test_console_reads_data.py` | **39 passed** |
| All console-reads tests (foundation, data, contract) | 119 passed |
| Full pytest | **1,099 passed, 0 failed** |
| Ruff (whole repo) / pyright (whole repo) | Same 2 existing errors / same 205 existing errors; none in new files |
| Architecture scripts and endpoint audit | Passed |

Bugs found and fixed by these tests before they could ship:

- `Row.count` is a tuple method, so `row.count` would have returned the method itself
  (found by pyright; fixed with tuple unpacking).
- The `alembic_version` read built an invalid `SELECT` (found by the overview test).
- Incident `summary` is unbounded `Text`; it is now capped at 2,000 characters with an
  ellipsis, inside the contract and Console limits.

Behaviour notes for Console:

- Roles are AgentGate's real membership roles (`owner`, `admin`, `security_manager`,
  `operator`, `approver`, `viewer`).
- Searches are case-insensitive contains-matches. `%` and `_` typed by an administrator match
  literally.
- Operations `state` is `critical` (an open high or critical incident), `degraded` (any open
  incident, unresolved delivery failure or failed run in 24 h) or `healthy`.
- Queue work-item statuses are lower-cased and merged (`Queued` and `queued` → `queued`).
  `destinationUrl` keeps only scheme, host, port and path.

Not verified yet: real PostgreSQL execution (no local PostgreSQL on this machine). Every
statement compiles for PostgreSQL, and the read-only and timeout statements are checked with a
recording connection. Actual execution against PostgreSQL happens in phase 10.

## Phases 6–8: usage, AI usage, security, notifications, search, support (10 October 2026)

All 16 contract operations are now served. New files:

- `backend/app/application/console_reads/analytics.py`: `usage`, `ai-usage`
- `backend/app/application/console_reads/security.py`: `security`, `notifications`
- `backend/app/application/console_reads/support.py`: `search`, `support-user`, `support-organization`
- `database.py` gained two dialect-aware expressions. `json_integer` reads a numeric JSON
  field and treats anything else as 0, so a bad value never breaks the sum. `utc_day` gives
  the UTC calendar day. Each compiles separately for PostgreSQL and SQLite.

Contract: 7 new `data` shapes plus 7 examples. `contract.v1.json` was re-serialised with standard
2-space JSON formatting.

Data handling decisions:

- `security`: only `id, organizationId, eventType, category, severity, correlationId,
  createdAt`, which are exactly the fields Console's audit page uses. Audit `actor`,
  `resource` and `payload` are never selected. Free-form JSON there could contain a key
  such as `token`, which would make Console reject the whole response.
- `ai-usage`: aggregates only. `request_id`, `transport_identity` and per-call records are
  never selected. Cost is always reported as unavailable.
- `notifications`: up to 20 open high or critical incidents, then up to 20 unresolved delivery
  failures, in Console's `ProductNotification` shape with stable fingerprints.
- `search`: case-insensitive contains-match on user name, email and subject and on organisation
  name. A pasted full UUID finds that exact record. The former shared-database search also
  matched partial IDs; that is not supported.

### Round trips (found by running against the real database)

You confirmed the Neon database in `backend/.env` is safe to read, so every operation was
run against it inside the read-only transaction (`pg_check.py`, run from `backend/`). The
script prints only names, counts, sizes and timings. Results:

- All 16 operations returned contract-valid data within Console's limits, on three runs.
- PostgreSQL refused a write inside the read transaction, and a 200 ms statement timeout
  was enforced and mapped to `TimeoutError`.
- Query execution on the server is under 1 ms (for example the AI summary: 0.5 ms over 626 rows).
- A network round trip from this machine to Neon took **139–1,618 ms**. Each read was
  costing several seconds purely in round trips: organisation detail took 9.3 s.

Fix: counts are now batched into single `SELECT`s of scalar subqueries, and directory pages
use correlated counts. Per-read statement counts are fixed by `test_round_trip_budget`:

| Read | Statements |
|---|---|
| snapshot, security | 1 |
| overview, organizations, operations, queues, notifications, search, support-user | 2 |
| users, support-organization | 3 |
| user, usage | 4 |
| ai-usage | 5 |
| organization | 7 (was 17) |

After the change, from this machine: organisation detail took 4.2–4.9 s (was 9.3 s) and overview
4.0 s (was 7.1 s). One run hit the 3 s statement limit on `ai-usage`; the next two runs passed. With
sub-millisecond execution, that was network or Neon wake-up time, not query cost. Hosted
next to the database, a round trip is a few milliseconds, so these reads should take well under a second.

| Check | Result |
|---|---|
| `test_console_reads_data.py` | **70 passed** (round-trip budgets included) |
| Contract tests | 29 passed |
| Full pytest | **1,137 passed, 0 failed** |
| Ruff / pyright (whole repo) | Same 2 / 205 existing errors; none in new files |
| Architecture scripts and endpoint audit | Passed |

## Phase 9: Console side (10 October 2026, in Audoryn-Console)

Built from the handover (C1–C6). See Audoryn-Console `docs/integration/agentgate-admin-reads.md`.

| Check (Audoryn-Console backend) | Result |
|---|---|
| `tests/test_agentgate_signed_reads.py` | **50 passed**. A fake AgentGate verifies every signature with Console's verifier and serves these contract examples |
| Full pytest | **236 passed, 0 failed** (one stale accessibility test updated: pagination moved to `components/console/Page.tsx`) |
| Ruff (`--ignore E501`) / pyright | Clean / 0 errors |
| Migration heads, secret scan, Phase F readiness | Passed |
| OpenAPI client `--check` | Drift from existing uncommitted CMS work (`POST /cms/releases/{release_id}/reopen`), unrelated to this phase; not regenerated |

## Phase 10: both running together locally (10 October 2026)

Setup: AgentGate API on 127.0.0.1:8010 and Console API on 127.0.0.1:8001, both with
process-only settings and a generated test key (no `.env` file changed). The AgentGate test
instance ran with `SMART_PLANNER_ENABLED=false` and `RUNTIME_EXECUTION_ENABLED=false`, so it
started no background work. Data came from the AgentGate development database (approved for
reads), and the replay and rate keys went to its Upstash Redis (approved). The owner's Console
frontend on :3000 was used, and the owner signed in. The test key, results and logs were deleted afterwards.

| Layer | Result |
|---|---|
| 1. Signed requests to the AgentGate server, all 16 reads | All contract-valid, including a search with space, `+` and `@` |
| 1. Rejections | Replayed, unsigned, query changed after signing, wrong-scope header: all 401 |
| 3. Console pages | Overview, users plus search, user detail, organisations, organisation detail, system health, queues, usage, AI usage, security events, notifications, support search (with `@`), support user, support organisation and the command-menu search all render, and their numbers match layer 1 |
| 4. AgentGate stopped | AgentGate pages show "awaiting source" / "not connected"; system health shows the connection "unavailable"; CMS, administrators, plans, feature flags, settings and Console health keep working |
| 4. Wrong key in Console | AgentGate answers 401; pages show "not connected", never wrong data |
| 4. Matching keys restored | Pages recover |
| Rate limit | 120 per minute, then 429 with `Retry-After` |
| 5. Logs | No key, signature or signing header in any log; application log lines hold no query values |

Fixed during this phase:

- **Console overview page crashed with real data.** It read `data.notYetModeled`, which the API
  never sent; it now shows the `consoleAuthority` the API returns
  (`frontend/src/app/(console)/overview/page.tsx`, `features/platform/types.ts`). This had never
  run before, because the page always took the "not connected" branch.
- **Refusals were invisible in logs.** Neither backend configures logging, so INFO lines are
  dropped and `extra` fields are not printed. Refusals and failures now log at WARNING with the
  fields in the message, on both sides; successful reads stay at INFO. Tests were updated.

Noted, not changed:

- Uvicorn's default access log prints full request URLs, so search terms and email filters
  appear in access lines (true for every route in both apps). Consider `--no-access-log` or a
  query-redacting access log for hosted environments.
- The rate limit uses a fixed one-minute window: a burst across a minute boundary can briefly get twice the limit.
- Console system health's "Console 0/5 dependencies available" tile disagrees with its own
  list (all healthy or configured). This is an existing counting issue, unrelated to AgentGate.
- From this machine, AgentGate-backed pages take about 2–10 s, entirely from network round trips to Neon.

Final regression: AgentGate pytest **1,137 passed**; Console pytest **236 passed**, ruff and
pyright clean; Console frontend `tsc --noEmit` clean.
