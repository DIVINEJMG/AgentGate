# Handover to Console: admin reads from AgentGate

Audience: whoever works on `Audoryn-Console` next. Date: 10 October 2026.
Plan and full contract: [console-admin-reads-plan.md](console-admin-reads-plan.md).
Status: **AgentGate side complete** (phases 0–8, local). All 16 operations are served, tested
and were run read-only against the development database. Console work (plan phase 9) can start.
Progress: [console-reads/checkpoints.md](console-reads/checkpoints.md).

## 1. What changes and why

Console's admin pages (overview, users, organisations, and later operations, usage, AI
usage, security, support, search, notifications) currently show "unavailable", because
`backend/app/bootstrap/agentgate.py` wires `UnconfiguredAgentGateReads`.

The owner approved, on 10 October 2026:

- AgentGate serves **signed, read-only** endpoints for these reads. This is an
  amendment to Console Decision 0001's "no Console-requested reads on AgentGate". The
  other principles still hold: no shared database, no copying customer data into Console,
  no commands, no entitlements.
- Reads only. AgentGate builds and tests **all 16 operations** first; Console then wires
  them in one go (plan phase 9), and both are run together locally (phase 10).
- No staging or production rollout in this work; the system is still in development.

Nothing in Console's domain types or API routes should need to change for overview, users
and organisations. The later groups may need small adjustments where Console's page
shapes and the AgentGate contract are pinned down; those are listed in the contract files
and in this document as each AgentGate phase lands. The core work is one new reader
adapter, its wiring and tests.

## 2. What AgentGate will provide

- Base: `{AGENTGATE_INTERNAL_ORIGIN}/internal/console/v1/{operation}`, `GET` only, query
  parameters only. Operation names and scopes are exactly Console's existing `OPERATIONS`
  map in `infrastructure/agentgate/reads.py`.
- Authentication: Console's existing `sign_read_request` scheme, unchanged. Audience
  `agentgate`. AgentGate verifies with its own copy and its own Redis for replay.
- Response: the `ReadEnvelope` Console already defines (`version, operation,
  requestTarget, requestId, observedAt, data`). For a detail read with an unknown ID,
  `data` is `null`.
- `data` for `snapshot`, `overview`, `users`, `user`, `organizations` and `organization` matches
  Console's dataclasses in `domain/audoryn/catalog.py` and `snapshot.py` **in
  snake_case**, so `RemotePlatformVisibilityRepository._decode` works unchanged.
- `health` returns `data: {"compatible": true}`, which `AgentGateConnectionProbe` expects.
- Errors: `{"version":1,"error":{"code","message"}}` with 404 `not_found` (feature off
  or unknown), 401 `unauthorized`, 400 `invalid_parameters`, 429 `rate_limited`
  (`Retry-After`), 503 `unavailable`, 504 `timeout`.
- Organisation detail lists are capped: members 200, workers 100, jobs 100,
  integrations 100, incidents 50. The full counts are in the directory item.
- Shared files in AgentGate `docs/integration/console-reads/`: `contract.v1.json` (JSON
  Schema), `examples/*.json`, and `signing-vector.json`. Copy them into Console's
  `fixtures/agentgate-reads/` and test against them.

### Notes from the AgentGate build (phases 6–8)

- `usage`: `windowDays` echoes `days` (1–90); `daily` has one zero-filled entry per UTC day.
  Console still adds its own `cmsPublications`, `paymentFailures`, `planDistribution` and
  `recognizedPayments`.
- `ai-usage`: the same keys Console's former SQL produced, including `estimatedCost: null`,
  `costAvailable: false` and `costNote`. `limit` is 1–100.
- `security`: `limit` is 1–200. Items carry only `id, organizationId, eventType, category,
  severity, correlationId, createdAt`, which are the fields the audit page renders. The former
  `actor`, `resource` and `payload` fields are gone; if any Console code reads them, it must stop.
- `notifications`: `limit` is 1–40 and items are `ProductNotification`s as Console defines them.
- `search`: `query` is 2–180 characters and `limit_per_type` 1–20. Partial-ID matching is not
  supported; a full UUID matches exactly.
- `support-user` / `support-organization`: the AgentGate half only, as Console's adapter
  expects; `data: null` when not found.
- **Timeouts when running locally:** from this machine, one round trip to the Neon development
  database takes 139–1,618 ms, and the slowest read (organisation detail, 7 statements) took
  up to about 5 s. For phase 10 on this machine, set `AGENTGATE_READS_TIMEOUT_SECONDS=15` in
  Console. Keep 5 for hosted environments.

### Notes from the AgentGate build (phases 3–5)

- `operations` and `queues` data is camelCase and matches the keys Console's
  `SeparatedObservabilityRepository` already checks. `consoleJobsPending`, `consoleJobsFailed`
  and `consoleJobs` stay Console-local and are merged by Console, as they are today.
- Delivery failures never include `failure_payload`. `destinationUrl` keeps only scheme,
  host, port and path; the queues page shows it unchanged.
- Incident summaries are capped at 2,000 characters (ending in "…" when cut).
- `queues?limit=` accepts 1–100, and Console's page asks for 100.

## 3. Console work, step by step

### C1. Settings (`bootstrap/settings.py`, `.env.example`)

| Setting | Default | Notes |
|---|---|---|
| `AGENTGATE_READS_ENABLED` | `false` | Without it, keep `UnconfiguredAgentGateReads` |
| `AGENTGATE_INTERNAL_ORIGIN` | unset | HTTPS origin of AgentGate's API (HTTP allowed only for localhost) |
| `AGENTGATE_READS_AUDIENCE` | `agentgate` | |
| `AGENTGATE_READS_KEY_ID` | unset | Same ID set on AgentGate |
| `AGENTGATE_READS_KEY_SECRET` | unset | Secret ≥32 bytes, base64url; `SecretStr` |
| `AGENTGATE_READS_KEY_SCOPES` | unset | Comma list, must not exceed what AgentGate grants |
| `AGENTGATE_READS_TIMEOUT_SECONDS` | `5` | Whole request |
| `AGENTGATE_READS_CACHE_SECONDS` | `30` | See C3 |

If it's enabled but any value is missing or invalid, fail at startup, with a message that
names the setting without showing its value.

### C2. `SignedAgentGateReads` (`infrastructure/agentgate/reads.py`)

Implements `AgentGateReadPort.read(operation, parameters)`:

1. Reject an operation not in `OPERATIONS` (`unsupported_operation`), and one whose scope
   isn't in the configured key scopes.
2. Build the target: `/internal/console/v1/{operation}`, then `?` + query when there are
   parameters. Query = parameters **sorted by key**, encoded with
   `urllib.parse.urlencode(sorted(items), quote_via=urllib.parse.quote, safe="")`.
   Values are `str(value)`; datetimes are already ISO strings from the caller.
3. `sign_read_request(key, target=target, scope=OPERATIONS[operation])`.
4. Send with `httpx.AsyncClient(base_url=origin, follow_redirects=False, timeout=…)`.
   Add `X-Request-ID` (new UUID hex) and `Accept: application/json`. Send the target
   **exactly as signed**: pass it as a full URL string built from origin plus target, never
   through httpx `params=`, which would re-encode it.
5. Read at most 1 MiB of the body; anything larger gives `invalid_response`.
6. Map the status to `AgentGateReadError(code, status_code=…)`:
   - 200 → continue;
   - 404 → `unconfigured` (503);
   - 401 → `unauthorized` (503; log it, it means the keys or clock don't match);
   - 400 → `invalid_parameters` (502);
   - 429 → `rate_limited` (503);
   - 503 → `unavailable`;
   - 504 or a client timeout → `timeout` (504);
   - a connection error → `unreachable` (503);
   - anything else → `invalid_response` (502).
7. Parse `ReadEnvelope` (`extra="forbid"`). Require `version == 1`, `operation ==
   operation` and `requestTarget == target`; otherwise `response_scope_mismatch`.
8. `validate_evidence(envelope.data)`, then return `envelope.data`.

Retry: only once, and only on a connection error before any response arrived. A signed
request can't be resent, so retrying means signing again with a new nonce. No
retry on timeout or 5xx: the dashboard refreshes on its own.

Never log the secret, the signature, query values (they can hold emails) or response bodies.
Log the operation, status, duration, request ID and error code.

### C3. Caching

Add a small in-process TTL cache in front of the signed reader, keyed by
`(operation, sorted parameters)`, for `snapshot`, `overview`, `operations`, `queues`,
`usage` and `ai-usage` only, for `AGENTGATE_READS_CACHE_SECONDS`. Don't cache detail,
directory or search reads (pagination and freshness matter there), and never cache errors.
Concurrent identical misses should share one request.

### C4. Wiring (`bootstrap/agentgate.py`)

`agentgate_reads()` returns the cached signed reader when `AGENTGATE_READS_ENABLED`
is true and all settings are present, otherwise `UnconfiguredAgentGateReads()`. Build the
client once per process and close it on shutdown (lifecycle). Readiness stays
Console-only. The AgentGate probe remains a separate integration status, as it is now.

### C5. Tests (deterministic, no network)

- The AgentGate `signing-vector.json` produces the same signature with Console's signer.
- Target building: sorting, encoding of spaces, `+`, `%`, `@`, unicode; no params → no `?`.
- Each example in `examples/` decodes through `RemotePlatformVisibilityRepository`.
- Envelope checks: wrong version, wrong operation, wrong `requestTarget`, extra fields.
- Status mapping for every row in C2, plus connection error, timeout and an oversized body.
- `validate_evidence` rejects a forbidden key, a list over 200, an object over 100 keys.
- Directory: more items than `limit`, or `total` below the item count → `invalid_response`.
- Detail: a mismatched ID → `response_scope_mismatch`; `null` → 404 from Console's API.
- Disabled or incomplete settings → unconfigured reader; startup error for half-set settings.
- The cache: hit, expiry, no caching of errors or detail reads, and a single request for concurrent misses.
- API level: `/api/v1/platform/overview`, `users`, `users/{id}`, `organizations` and
  `organizations/{id}` return real data through a fake transport, and return the existing
  "unavailable" behaviour when AgentGate is off.

### C6. Documents to update in Console

- `docs/decisions/0001-…`: add an amendment dated 10 October 2026 for signed read-only
  AgentGate endpoints (reads only; commands and entitlements unchanged).
- `docs/architecture/separation-contract-preparation.md`: mark "Private visibility v1"
  as selected, link this handover and AgentGate's plan.
- README "Approved architecture update" paragraph: one line noting the change.

## 4. Running both locally (plan phase 10)

1. Generate a test key: `python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`.
2. AgentGate backend: `CONSOLE_READS_ENABLED=true`, `CONSOLE_READS_KEY_ID=console-reads-local`,
   the secret, and `CONSOLE_READS_KEY_SCOPES=visibility.read,observability.read,security.read,notifications.read`.
3. Console backend: `AGENTGATE_READS_ENABLED=true`,
   `AGENTGATE_INTERNAL_ORIGIN=http://localhost:<agentgate api port>`, and the same key ID, secret and scopes.
4. Use local or development data only. Check every page in §5 against what the AgentGate
   workspace shows for the same data, including a search with spaces, `+` and `@`.
5. Turn AgentGate's switch off: those pages show "unavailable", and CMS, administrators,
   audit, billing, flags and announcements still work.

Use process-local environment variables or untracked `.env` files. Never commit keys.
Staging and production are not part of this work. When they happen later, each gets its
own new key, which the owner sets on the hosts.

## 5. Pages and the AgentGate phase that serves them

| Console page | Uses | AgentGate phase |
|---|---|---|
| Overview | `overview`, `snapshot` | 3 |
| Users, user detail | `users`, `user` | 4 |
| Organisations, organisation detail | `organizations`, `organization` | 4 |
| Operations, system health, queues | `operations`, `queues`, `health` | 5 (`health`: done in 2) |
| Usage, AI usage | `usage`, `ai-usage` | 6 |
| Security (product events), notifications refresh | `security`, `notifications` | 7 |
| Search (AgentGate half), support pages | `search`, `support-*` | 8 |
| CMS, content, commercial, flags, announcements, administrators, audit, settings | Console only | Already working; unaffected |

Console starts after phase 8, so all of these are available to it at once. With AgentGate
switched off, the pages show "unavailable", which is correct, not a bug.

## 6. Not included

Commands (suspend, etc.), entitlements, flag and announcement delivery to the workspace,
copying data into Console, and impersonation. Each needs its own approval and contract.
