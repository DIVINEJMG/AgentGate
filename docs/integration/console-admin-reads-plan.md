# Console admin reads: implementation plan

Status: **all phases (0–10) done locally** on 10 October 2026; see [checkpoints](console-reads/checkpoints.md). Off by default on both sides. Staging and production are a later, separate step.
Order (revised 10 October 2026): AgentGate phases 3–8 for all page groups → Console (9) → both together locally (10). No staging or production in this work.
Companion document for the Console side: [console-admin-reads-handover.md](console-admin-reads-handover.md).

## 1. Aim

Audoryn Console is the private panel for platform staff. Its CMS link to the public
website is done and tested. This work connects the rest of Console (overview, users,
organisations, then operations, usage, AI usage, security, support, search and
notifications) to real AgentGate data.

Decisions made on 10 October 2026:

1. AgentGate may serve **signed, read-only** data to Console. This supersedes the
   "no Console-requested reads on AgentGate" line in Console's Decision 0001 for these
   reads only. Customer data is still not copied into Console's database.
2. This work is **reads only**. Feature flags, announcements, entitlements and admin
   actions (suspend, etc.) are later, separately approved work.
3. First group: **overview, users and organisations**.
4. Work starts in AgentGate and covers **every page group** there first. Console follows
   from the handover document, then both are run together locally.
5. Everything is local for now. Staging and production are a later, separate step
   (decided 10 October 2026: the system is still in development).

## 2. What must not break

| Rule | How this plan keeps it |
|---|---|
| Existing AgentGate routes, auth and workers are unchanged | All new code lives in new files under `backend/app/api/internal/console_reads.py` and `backend/app/application/console_reads/`. Existing files only gain an import line (`api/router.py`) and new optional settings (`bootstrap/settings.py`). |
| The user's uncommitted backend changes are preserved | Additive edits only; never revert or reformat existing lines. `router.py` and `settings.py` already have the user's changes. |
| Nothing is reachable until switched on | `CONSOLE_READS_ENABLED=false` by default. With it off, or with no key configured, every new route answers 404, the same as a route that doesn't exist. |
| Customer reads cannot write | Each request uses its own session with `SET TRANSACTION READ ONLY` and `SET LOCAL statement_timeout`. No ORM objects are added or flushed. Tests assert both. |
| No secrets or private content leave AgentGate | Responses are built field by field from fixed allowlists (never `SELECT *` or model dumps). Password hashes, credential configs, prompts, message bodies, tokens and integration `config` are never selected. |
| AgentGate stays healthy under Console load | Fixed page and window caps, a database time limit, a per-key rate limit, and Console-side caching (see the handover). |
| Console keeps working when AgentGate is down | Console already shows "unavailable" per page; this plan doesn't change that. |
| Either side can turn it off | AgentGate: set `CONSOLE_READS_ENABLED=false` or remove the key. Console: remove its AgentGate settings, and it falls back to the existing "unconfigured" reader. |

## 3. Transport contract (v1)

### 3.1 Routes

All routes are `GET`, have no body, and live under `/internal/console/v1/`. Each operation is
its **own declared route**: there is no catch-all, no proxy, no method or path parameter.
Parameters are query-string only.

| Operation | Route | Scope | Phase |
|---|---|---|---|
| `health` | `/internal/console/v1/health` | `observability.read` | 2 |
| `snapshot` | `/internal/console/v1/snapshot` | `visibility.read` | 3 |
| `overview` | `/internal/console/v1/overview` | `visibility.read` | 3 |
| `users` | `/internal/console/v1/users` | `visibility.read` | 4 |
| `user` | `/internal/console/v1/user?user_id=` | `visibility.read` | 4 |
| `organizations` | `/internal/console/v1/organizations` | `visibility.read` | 4 |
| `organization` | `/internal/console/v1/organization?organization_id=` | `visibility.read` | 4 |
| `operations` | `/internal/console/v1/operations` | `observability.read` | 5 |
| `queues` | `/internal/console/v1/queues?limit=` | `observability.read` | 5 |
| `usage` | `/internal/console/v1/usage?days=` | `observability.read` | 6 |
| `ai-usage` | `/internal/console/v1/ai-usage?days=&limit=` | `observability.read` | 6 |
| `security` | `/internal/console/v1/security?limit=` | `security.read` | 7 |
| `notifications` | `/internal/console/v1/notifications?limit=` | `notifications.read` | 7 |
| `search` | `/internal/console/v1/search?query=&limit_per_type=` | `visibility.read` | 8 |
| `support-user` | `/internal/console/v1/support-user?user_id=` | `visibility.read` | 8 |
| `support-organization` | `/internal/console/v1/support-organization?organization_id=` | `visibility.read` | 8 |

The operation names and scopes match Console's existing `OPERATIONS` map in
`Audoryn-Console/backend/app/infrastructure/agentgate/reads.py`. The required scope
always comes from the route AgentGate matched, never from the caller's header.

### 3.2 Request signing

This is the same scheme Console already implements and tests (`service_signing.py`). AgentGate gets
its own copy of the verifier; it does not import Console code.

- Headers: `X-Audoryn-Service-Key`, `-Audience`, `-Scope`, `-Timestamp`, `-Nonce`,
  `-Signature` (all prefixed `X-Audoryn-Service`). Duplicate headers are rejected.
- Signature: `v1=` + hex HMAC-SHA256 over ASCII lines joined by `\n`:
  `audoryn-service-v1`, key ID, audience, scope, `GET`, target, timestamp, nonce,
  SHA-256 hex of the empty body.
- **Target**: the exact encoded path plus `?` plus the raw query string, taken from
  `request.scope["raw_path"]` and `request.scope["query_string"]` as received. AgentGate
  never re-encodes or reorders it. Console must send the query exactly as it signed it
  (sorted keys, RFC 3986 percent-encoding, see the handover).
- Audience: `agentgate`. Clock skew: 60 s (configurable 1–300). Nonce: 32 lowercase hex.
- Replay: AgentGate's own Redis, `SET console-reads:replay:{key_id}:{nonce} 1 NX EX 121`,
  claimed only after the signature checks out. If Redis errors, the request is refused
  (fail closed).
- Keys: at least 32 random bytes, separate from every other AgentGate secret. Up to two
  active key IDs for rotation. Each key lists its scopes explicitly; there is no wildcard.
- A shared **test vector** (fixed key, timestamp, nonce, target, expected signature) is
  stored in `docs/integration/console-reads/signing-vector.json`. Both repos test
  against it, which proves the two implementations agree byte for byte.

### 3.3 Responses

Success (`200`), media type `application/json`, `Cache-Control: no-store`:

```json
{
  "version": 1,
  "operation": "users",
  "requestTarget": "/internal/console/v1/users?limit=50&offset=0",
  "requestId": "c0a8…",
  "observedAt": "2026-10-10T12:00:00Z",
  "data": { }
}
```

This matches Console's `ReadEnvelope` model exactly (extra fields are forbidden).
`requestTarget` echoes the signed target, so Console can check the answer belongs to its
own request. `requestId` is `X-Request-ID` if Console sent a valid one (≤64 chars,
`[A-Za-z0-9_.-]`), otherwise a new UUID.

A detail read for an ID that doesn't exist returns `200` with `"data": null`, because
Console's types expect `UserDetail | None`.

Errors return `{"version":1,"error":{"code":"…","message":"…"}}`. The messages are generic and never include secrets:

| Status | Code | When |
|---|---|---|
| 404 | `not_found` | Feature off, no key configured, or unknown route |
| 401 | `unauthorized` | Any signing failure. The reason goes to the log only and is never returned. |
| 400 | `invalid_parameters` | A parameter outside its bounds or type |
| 429 | `rate_limited` | Over the per-key limit; includes `Retry-After` |
| 503 | `unavailable` | Database or Redis unavailable |
| 504 | `timeout` | Database statement timeout |

### 3.4 Limits that Console already enforces

Console rejects a whole response that breaks any of these. AgentGate must stay inside
them by construction:

- any object ≤ 100 keys; any list ≤ 200 items; any string ≤ 8,192 chars; nesting ≤ 16;
- no key named `password`, `secret`, `token`, `access_token`, `refresh_token`,
  `authorization`, `prompt`, `messages`, `credentials`, `private_key`;
- directory pages ≤ 100 items, and `total` ≥ the number of items returned;
- detail responses must carry the requested ID (otherwise Console reports `response_scope_mismatch`).

AgentGate also caps the serialized response at 1 MiB.

### 3.5 Data shapes for phases 3–4

The `data` for visibility operations uses **snake_case** field names. Console decodes it with a strict
`TypeAdapter` against its dataclasses in `app/domain/audoryn/catalog.py` and
`snapshot.py`, so field names, types and nullability must match exactly. UUIDs and
datetimes are ISO strings (datetimes in UTC with offset). Tuples are JSON arrays.

- `snapshot` → `{human_identities, organizations}` (ints).
- `overview` → `PlatformOverview`: `total_users, active_users_30d,
  users_with_memberships, organizations, active_organizations_30d, workers, runs_30d,
  failed_runs_30d, new_registrations_30d, integrations, open_incidents,
  unresolved_queue_failures` (ints), `agentgate_migration_heads` (string array).
- `users` → `[items, total]`: a two-element array of `UserDirectoryItem[]` and an int.
  Each item has `id, subject, email|null, display_name|null, created_at, updated_at,
  organization_count, membership_roles[]`. Parameters: `query` ≤160, `email` ≤320,
  `registered_from`, `registered_to` (ISO), `limit` 1–100, `offset` ≥0.
- `user` → `UserDetail | null`: `user` (the directory item), `memberships[]`
  (`organization_id, organization_name, role, joined_at`), `activity`
  (`organizations_created_30d, conversations_started_30d, result_exports_30d,
  approval_decisions_30d, last_activity_at|null`), `local_password_configured` (bool,
  existence only; the hash is never read).
- `organizations` → `[items, total]`, `OrganizationDirectoryItem`: `id, name,
  created_by, created_at, updated_at, member_count, worker_count, job_count, runs_30d`.
  Parameters: `query` ≤160, `limit` 1–100, `offset` ≥0.
- `organization` → `OrganizationDetail | null`: `organization`, `members[]` (≤200,
  `user_id, email, display_name, role, joined_at`), `workers[]` (≤100, `id, name,
  department, status, created_at, updated_at`), `jobs[]` (≤100, `id, worker_id, name,
  status, current_revision, created_at, updated_at`), `integrations[]` (≤100, `id,
  provider, display_name, status, created_at, updated_at`; never `config`),
  `incidents[]` (≤50), `usage` (`runs_30d, failed_runs_30d, ai_invocations_30d`),
  `operational_state` (`state` = `attention` if open incidents, `degraded` if
  unresolved queue failures or failed runs in 24 h, else `healthy`; plus the three
  counts), `audit_summary` (`events_30d, high_or_critical_30d`).

The query semantics are the same as Console's former shared-database adapter
(`Audoryn-Console/backend/app/infrastructure/database/platform_visibility.py`). That file
is the reference for what each number means. Every table and column it used was checked
against `backend/app/infrastructure/database/models.py` on 10 October 2026 and exists.
Member, integration and worker lists are capped. Console shows the full count from the
directory item (`member_count`, …), so a capped list is visible as "showing N of M".

Shapes for phases 5–8 are added to the contract when those phases start. Console's checks in
`observability.py` and `notifications.py` already pin their top-level keys (see the handover).

## 4. Configuration (AgentGate backend)

| Setting | Default | Notes |
|---|---|---|
| `CONSOLE_READS_ENABLED` | `false` | Master switch |
| `CONSOLE_READS_AUDIENCE` | `agentgate` | Must match Console |
| `CONSOLE_READS_KEY_ID` | unset | e.g. `console-reads-1` |
| `CONSOLE_READS_KEY_SECRET` | unset | Secret, ≥32 bytes, base64url |
| `CONSOLE_READS_KEY_SCOPES` | unset | Comma list, e.g. `visibility.read,observability.read` |
| `CONSOLE_READS_PREVIOUS_KEY_ID` / `_SECRET` / `_SCOPES` | unset | Only during a rotation |
| `CONSOLE_READS_CLOCK_SKEW_SECONDS` | `60` | 1–300 |
| `CONSOLE_READS_RATE_PER_MINUTE` | `120` | Per key |
| `CONSOLE_READS_STATEMENT_TIMEOUT_MS` | `3000` | Per request |

Placeholders go in `.env.example` only. Real values are set by you on the host, never in the repo.

## 5. Phases

Each phase ends with its checks passing and a short report to you before the next one starts.
Nothing is committed or pushed without your approval.

### Phase 0: baseline (AgentGate)

- Create a local backend environment the same way CI does (`pip install -e ".[dev]"` in
  a new `backend/.venv`; nothing on this machine has it yet).
- Run and record: `ruff check app tests migrations`, `pyright`, `pytest`,
  `pytest tests/contract`, and the `scripts/verify_f28_*` architecture checks that CI runs.
  Failures that existed before this work are recorded as "existing" and left alone.
- Frontend: record `npm run typecheck`, `npm test`, `npm run build` (unchanged by this
  work, but proves nothing regressed).

**Exit:** a baseline table in `docs/integration/console-reads/checkpoints.md`.

### Phase 1: contract files

- `docs/integration/console-reads/contract.v1.json`: JSON Schema for the envelope, the
  error body and the `data` shapes (phases 3–4 first; phases 5–8 add theirs).
- `docs/integration/console-reads/examples/*.json`: one valid example per operation.
- `docs/integration/console-reads/signing-vector.json`: the fixed signing test vector.
- Test: each example validates against the schema and against Console's limits (§3.4).

**Exit:** contract tests pass. These files are what Console builds against.

### Phase 2: receiver foundation (no customer data yet)

- `app/application/console_reads/signing.py`: the verifier and the key set from settings.
- `app/application/console_reads/guard.py`: the FastAPI dependency (feature switch,
  verify, replay claim, rate limit, request ID, structured log).
- `app/api/internal/console_reads.py`: router with only `health` →
  `{"compatible": true}`, which is exactly what Console's `AgentGateConnectionProbe` expects.
- Settings and `.env.example` placeholders; one `include_router` line.
- Tests (about 30): switch off → 404; no key → 404; good signature → 200; wrong
  audience, scope, key, timestamp, nonce format or signature → 401; changed path or query
  → 401; reused nonce → 401; Redis down → 503; the signing vector matches; the scope comes
  from the route, not the header; a header-supplied scope with more access doesn't help; rate limit
  → 429; responses carry `no-store`; logs contain no secrets or query values.

**Exit:** tests and baseline checks pass; with default settings the app behaves exactly as before.

### Phase 3: overview and snapshot

- `app/application/console_reads/visibility.py`: read-only session helper (read-only
  transaction plus statement timeout) and the two queries.
- Routes `snapshot`, `overview`.
- Tests: shapes validate against the contract; counts are correct on seeded data;
  the transaction is read-only (an attempted write fails); timeout → 504.

### Phase 4: users and organisations

- Directory and detail queries with bounded parameters, stable ordering
  (`created_at DESC, id`), the list caps from §3.5, and `data: null` for unknown IDs.
- Search parameters are bound values only, never formatted into SQL. `%` and `_` in
  user input are escaped for `ILIKE`.
- Tests: paging and totals; filters; caps; unknown ID; the wrong ID never leaks another
  record; no password hash, integration config or other excluded field appears anywhere
  in the output (checked by scanning the serialized JSON).

### Phases 5–8: the remaining page groups (AgentGate side, local)

Each phase follows the same steps as phases 3–4: add the `data` shape to
`contract.v1.json` with examples, build the AgentGate route and queries, add tests,
update the checkpoint and report. The exact shapes are taken from what Console's existing
code checks (`infrastructure/agentgate/observability.py`, `notifications.py`) and what its
pages render, and from Console's former SQL in `platform_observability.py` and `platform_admin.py`.

- **5 Operations and queues:** incident and queue-failure counts, failed runs in 24 h, queued work
  items, unpublished outbox, recent incidents (summaries only); queue failure list
  without `failure_payload` or destination URLs containing credentials.
- **6 Usage and AI usage:** daily aggregates over ≤90 days; AI by provider, model,
  organisation and worker, with success, latency and token counts. No prompts or
  responses. Cost stays "unavailable" (AgentGate doesn't record prices).
- **7 Security events and notifications:** ≤200 sanitised audit event summaries (no
  `payload`); product notifications using Console's `ProductNotification` shape
  and fingerprints.
- **8 Search and support:** users and organisations by name or email, ≤20 per type; support
  identity, memberships, organisation state and incidents. No impersonation.

**Exit for the AgentGate side:** all 16 operations served and tested, the checkpoint
updated, the handover finalised, and a report to you. Then we move to Console.

### Phase 9: Console side (see handover)

The signed reader, wiring, caching and tests in the Console repo, for every operation at
once, built from the handover document and the shared contract files.

### Phase 10: run both together locally

AgentGate backend and Console backend on this machine, sharing a generated test key and
pointed at **local or development data only**. Check every Console page that uses
AgentGate data (overview, users, organisations, operations, queues, usage, AI usage,
security, notifications, search, support) against what the AgentGate workspace shows for
the same data. Then switch AgentGate off and check those pages show "unavailable" while
CMS and admin pages keep working. Also check a query with spaces, `+` and `@` end to end.

### Later, not part of this work: staging and production

The system is still in development, so no staging or production rollout is planned now.
When it's wanted, it is a separate step you approve: a new key per environment (never
reused), set by you on the hosts, the same checks as phase 10, plus measuring the overview
and usage queries on real data volumes. Rollback is turning the switch off on either side.

## 6. Risks and how they are handled

| Risk | Handling |
|---|---|
| A proxy or host re-encodes the URL and breaks signatures | The verifier uses the raw path and query as received. Phase 10 checks a query with spaces, `+` and `@` end to end; repeated on any hosted environment later. |
| Clock drift between Render services | 60 s window; the probe reports `unauthorized`, which shows up on Console's readiness view. |
| Heavy queries on a large database | Statement timeout, page caps, rate limit and Console caching. Query cost is measured on real data volumes before any hosted rollout. |
| Personal data in logs | Only the operation, key ID, status, duration and request ID are logged; query values never are. |
| The contract drifts between repos | Schema, examples and signing vector are shared files, and both repos test against the same copies. |
| The user's in-progress AgentGate changes | Additive edits only. Any conflict in `router.py` or `settings.py` is raised with you, not resolved silently. |

## 7. Out of scope for this work

Entitlements, feature-flag and announcement delivery, admin commands (suspend, etc.),
copying AgentGate data into Console, changes to the customer workspace UI, deployment,
and setting real credentials. Each of these needs its own approval.
