# Commercial plans, limits and the Console: research

Written 16 October 2026. Research only: nothing here is built yet. Decisions for the owner are
marked **Decision**.

Goal: AgentGate sells plans. The Console decides what each organisation may use (its plan,
subscription and limits), AgentGate enforces it, usage resets each period, and the Console can
see and manage it.

## 1. What exists today

### 1.1 Console (Audoryn-Console)

The commercial domain is built and tested, but connected to nothing (F26–F31,
`docs/architecture/f26-f31-commercial-platform.md`):

| Part | What it holds |
|---|---|
| Plans (`console.billing_plans`) | Code, name, status (draft/active/archived), currency, monthly and annual price in minor units, optional worker limit, display order |
| Entitlement definitions | Named, typed limits: boolean (`browser.enabled`), integer (`workers.max`, `jobs.monthly`), string |
| Plan entitlements | The value of each entitlement on each plan, type-checked |
| Billing customers | Optional AgentGate organisation id (a reference, no foreign key) |
| Subscriptions | Plan, status (trialing, active, past_due, paused, cancelled, expired), monthly/annual/custom cycle, quantity, current period start and end, cancel at period end, full change history |
| Payment ledger | Payments, attempts, invoices, refunds, webhook events; append-only |
| Billing provider | An interface (`create_checkout`, `verify_webhook`, `refund_payment`, …) with no provider plugged in |

Every commercial change needs a Console administrator, a permission, a reason and an audit event.

### 1.2 The rules already agreed (Console Decision 0001)

- AgentGate owns customers, workers, jobs, runs and execution; the Console owns commercial
  administration.
- **Entitlements (Console → AgentGate):** after a separately approved cutover, the Console sends
  verified, versioned entitlement snapshots. AgentGate enforces them locally and never calls the
  Console for each action. Older versions are rejected and nothing is applied twice. Expiry,
  revocation, last-known state and fail-closed behaviour must be specified before it is switched
  on. Capacity is never granted from website pricing content.
- **Administrative commands** (for example resetting an organisation's usage) are a separate
  channel: requester, target, reason, idempotency key, durable outcome. Not yet authorised.
- Separate channels for public content, private reads, entitlements and commands; no generic
  admin API.

### 1.3 Existing connections between the two

| Link | Direction | How |
|---|---|---|
| CMS public website | Console publishes → AgentGate public frontend pulls | Versioned, checksummed releases on a content host; the frontend fetches a pointer (60 s cache), verifies hashes, keeps the last verified release on failure. AgentGate's backend is not involved. |
| Admin reads | Console → AgentGate backend | 16 signed (HMAC-SHA256), read-only `GET /internal/console/v1/*` endpoints: overview, users, organisations, operations, queues, **usage**, **ai-usage**, security, … Off by default; live on Oracle since 11 October. |

The CMS is the model for "publish a versioned document, the other side pulls, verifies and keeps
the last good copy". Entitlements are private, so they cannot use the public content host.

### 1.4 AgentGate

**Organisations** have no plan, subscription or period of their own (`Organization` is just a
name and creator).

**A hard-coded plan list exists** (`app/api/product_ops_routes.py`, `PLANS`): Free ($0, 3 workers),
Team ($49, 15), Business ($199, 75), Scale ($499, 250). The "Usage & billing" settings tab
(`CommercialPanel.tsx`) shows every organisation as **Free** and reports
`enforcement: "worker_limit_active"`, **but nothing enforces it**: `create_worker` never checks a
limit. The list also duplicates the Console's catalogue. Both must go when the Console becomes the
source of plans. (The panel also loads up to 1,000 runs and actions on every view: worth fixing at
the same time.)

**Limits that already exist** are technical safety limits, the same for every organisation, from
settings:

| Setting | Default | Where it is enforced |
|---|---|---|
| `smart_planner_org_tokens_per_day` | 50,000,000 AI tokens a day | AI budget reservation before every model call (Redis, `infrastructure/ai/workloads.py`) |
| `smart_planner_org_requests_per_minute` | 20 | same |
| `coding_organization_daily_seconds` | 7,200 s a day | coding sandbox reservation (`execution/coding/service.py`, `CodingBudget` table) |
| `coding_organization_concurrency` | 1 | coding sandbox admission |
| `web_research_daily_limit_per_organization` / hourly | 100 / 20 | web research |
| `integration_organization_rate_per_minute` | 60 | integration calls |
| `browser_max_sessions_per_organization` | 1 | browser session admission |

These are the right hooks: a plan mostly means making these numbers per-organisation, adding
monthly allowances, and adding count limits.

**Usage is already recorded:** every runtime step, chat message and task preparation writes a
`usage_records` row (AI calls, tokens in/out, models, prompt size by section, database statements,
queue messages, Redis commands, duration), and every model call writes `ai_invocations`. Not
recorded yet: browser time and coding sandbox time per task (coding has `active_seconds` per
session).

**Where countable things are created (single points to check a limit):**

| Thing | Created in | Points |
|---|---|---|
| Workers | `create_worker` (`api/workforce_routes.py`), used by the API, chat, templates, onboarding and autonomy | 1 |
| Tasks (work items) | `queue_job` (most paths), `integration_events` (event triggers), `integration_tasks` (chat integration tasks), `WorkItemDispatchRepository` (schedules) | 4 |
| Members | organisation creation, invitation join (`product_ops_routes`) | 2 |
| Connections | `integration_capability_routes`, `browser_origin_authority`, `github_connector`, `worker_autonomy` | 4 |
| Tools a task may use | the capability catalogue at task preparation, which already supports `capabilityExclusions` with a reason | 1 |

## 2. What work costs (measured)

Production usage records, read-only, 13–16 October 2026. **Caveat:** 4 days, 6 organisations,
mostly the owner's own testing plus a copy of simulation data. It shows the shape of costs, not
customer averages. Repeat with real customers before fixing prices.

| Unit | Count | AI calls | AI tokens (median) | AI tokens (90th percentile) |
|---|---|---|---|---|
| Chat message | 115 | 1.6 | 14,200 | 27,800 |
| Task preparation | 78 | 1.0 | 10,900 | 25,000 |
| One runtime step (avg) | 550 | 0.6 | ~4,200 (mean) | 24,800 |
| Browser task (whole) | 12 | 5–6 | 58,000–74,000 | 119,000 |
| GitHub task (whole) | 43 | 6–7 | 12,000 | 82,000–165,000 (max 184,000) |
| Coding session | 7 | – | – | avg 753 s active, max 1,125 s |

- **Tokens are ~95% input** (prompts), ~5% output. Price plans on input-token cost.
- **Per organisation per day:** median 225,000 tokens; 90th percentile 1.19 million; busiest day
  2.25 million tokens, 23 tasks and 49 chat messages.
- **Model mix:** Claude Sonnet 2.49M input tokens, Nemotron 1.43M, Claude Haiku 0.72M; the rest small.
- The current safety limit (50M tokens a day) is ~20× the busiest real day: in practice unlimited.
- Each step also costs ~1 queue message (QStash), 44–75 database statements and some Redis
  commands. These matter on the free tiers of QStash, Neon and Upstash.

**Cost per unit** = tokens × the provider's price per token (OpenRouter and NVIDIA dashboards),
plus E2B sandbox seconds for coding, plus a share of the Oracle server (fixed; the browser is
limited by its memory) and storage. The provider prices are not in the code, so the cost model
should take them as inputs, not as constants.

## 3. Proposed design

### 3.1 Who owns what

- **Console:** plans, entitlement definitions and values, subscriptions, periods, payments. The
  only place a plan is changed.
- **AgentGate:** keeps a local copy of each organisation's current entitlements, counts usage,
  enforces, and reports usage back through the existing signed reads.
- The hard-coded `PLANS` list in AgentGate is removed; the "Usage & billing" tab shows the
  delivered plan and real usage.

### 3.2 The entitlement snapshot

One versioned document per organisation, sent by the Console:

```json
{
  "contract": "audoryn.entitlements.v1",
  "organizationId": "…",
  "version": 7,
  "issuedAt": "2026-11-01T00:00:00Z",
  "subscription": {"planCode": "team", "status": "active",
                   "periodStart": "2026-11-01T00:00:00Z", "periodEnd": "2026-12-01T00:00:00Z"},
  "validUntil": "2026-12-08T00:00:00Z",
  "entitlements": {"workers.max": 15, "tasks.monthly": 2000, "ai.tokens.monthly": 50000000,
                   "browser.enabled": true, "coding.enabled": false}
}
```

- **Version** only goes up; AgentGate rejects an older or equal version (no double application).
- **Period start and end** come from the subscription: that is when monthly usage resets.
- **Valid until** is the period end plus a grace period. If no newer snapshot arrives by then
  (for example a lapsed subscription), AgentGate falls back to the default plan.
- **Fail-closed:** an organisation with no valid snapshot uses the default (free) entitlements,
  never "unlimited". The default plan is itself a Console-issued snapshot, so even the free tier is
  defined in one place.

### 3.3 Delivery: three options

| Option | How | For | Against |
|---|---|---|---|
| **A. Console pushes** (recommended) | Signed `POST /internal/console/v1/entitlements` (new, narrow, its own key and scope); AgentGate validates, applies, answers with the applied version. The Console confirms through the existing signed read of the organisation, which gains `entitlementVersion` | Immediate; reuses the HMAC signing both sides already have; matches Decision 0001 (accepted → verified) | First write endpoint from the Console; needs approval |
| B. AgentGate pulls | AgentGate calls a signed Console endpoint on a schedule and on demand | AgentGate controls timing | A new AgentGate → Console dependency; the Console must be reachable from Oracle; slower to apply a change |
| C. Private published file (CMS-style) | The Console writes signed snapshots to a private bucket; AgentGate polls | Mirrors the CMS | Polling cost and delay; another storage credential in AgentGate |

Recommended: **A**, plus a nightly reconcile (the Console resends any organisation whose applied
version is behind), so a lost delivery fixes itself.

### 3.4 Counting and resets

- A new `organization_usage` table: one row per organisation per period, with counters (tasks
  started, AI tokens, coding seconds, browser minutes, web research). It is updated where usage is
  recorded today, in the same transaction.
- Fast checks (AI tokens before every model call) stay in Redis, as now, with the database row as
  the record. Redis keys become per-period instead of per-day.
- **Reset** happens automatically when a snapshot with a new period starts. A manual reset (for
  example a goodwill top-up) is an administrative command: a separate, later approval.
- Count limits (workers, members, connections) need no counter: AgentGate counts the rows.

### 3.5 Enforcement points and behaviour at a limit

| Limit | Checked at | At the limit |
|---|---|---|
| `workers.max` | `create_worker` | Refused: "Your plan includes N workers." Existing workers keep working |
| `members.max` | invitation and join | Invitation refused |
| `connections.max` | the 4 connection-creation points | Refused |
| `tasks.monthly` | the 4 task-start points | New tasks are not started; a scheduled run is skipped with a notice in its chat; **running tasks finish** |
| `ai.tokens.monthly` | the AI budget reservation | Planning pauses (`waiting_ai` already exists) with a plain message; a small reserve keeps chat able to explain |
| `browser.enabled`, `coding.enabled`, `events.enabled` | the capability catalogue at task preparation | The tool is excluded with "Not included in your plan" (the `capabilityExclusions` mechanism exists) |
| `coding.seconds.monthly`, `browser.sessions.concurrent`, `web_research.monthly` | their existing budgets, made per-organisation | As today ("budget exhausted; this task will wait") |

Warnings at 80% and 100% go to the organisation's owners (in the workspace, and by email later).

### 3.6 Proposed entitlement keys

| Key | Kind | Notes |
|---|---|---|
| `workers.max` | integer | The Console plan already has `worker_limit` |
| `members.max` | integer | |
| `connections.max` | integer | |
| `tasks.monthly` | integer | Every task, however started |
| `ai.tokens.monthly` | integer | The real cost driver; could be shown to customers as "AI credits" |
| `ai.models.premium` | boolean | Whether Claude-class models are used, or only the cheaper ones |
| `browser.enabled` / `browser.sessions.concurrent` | boolean / integer | |
| `coding.enabled` / `coding.seconds.monthly` | boolean / integer | E2B costs real money per second |
| `events.enabled` | boolean | React to GitHub and other events |
| `schedules.enabled` / `schedules.min_interval_minutes` | boolean / integer | |
| `web_research.monthly` | integer | |
| `storage.bytes` | integer | Files and artifacts |

## 4. Suggested order of work (each phase approved separately)

1. **Measure better** (AgentGate, small): record browser and coding time per task in
   `usage_records`, and a simple cost report per organisation and period.
2. **Entitlement store and enforcement, off by default** (AgentGate): snapshot table, usage
   table, every check above behind one switch; with no snapshot and the switch off, nothing
   changes. Tests for every limit.
3. **Delivery endpoint** (AgentGate) and **sender** (Console), with the read-back of the applied
   version. Needs approval of the first Console → AgentGate write.
4. **Cutover:** issue a snapshot for every organisation (the default plan), switch enforcement on,
   remove the hard-coded `PLANS`, show the real plan and usage in "Usage & billing".
5. **Payment provider** (Console): checkout, renewal, failed payment → `past_due` → grace →
   downgrade, all ending in a new snapshot.
6. **Administrative commands** (later): manual reset, top-up, temporary extension.

Plans can be sold manually (a Console administrator sets the subscription) from phase 4, before
any payment provider exists.

## 5. Decisions for the owner

1. **What to charge for:** per worker, per task volume, per AI usage, or a mix? (The data says AI
   tokens are the real cost, workers are not.)
2. **Payment provider:** Paystack, Flutterwave, Stripe, or manual first?
3. **At a limit:** running tasks finish and only new work is blocked (recommended), or everything
   stops?
4. **Free tier or trial:** which, and what does it include?
5. **Delivery:** approve option A (the Console pushes signed snapshots to AgentGate)?
6. **Period:** each subscription's own monthly date (recommended, it follows the Console's
   `current_period_start`) or calendar months?
7. **Premium models:** should cheaper plans use only cheaper models, or the same models with lower
   token allowances?
