# Commercial plans, limits and the Console: implementation plan

Written 16 October 2026, from `docs/plans/commercial-plans-research.md` and the owner's decisions
of the same day. Each phase is built, tested (tests, then the simulator), handed over for Oracle,
and approved before the next one starts.

## 1. Decisions (owner, 16 October 2026)

| # | Decision |
|---|---|
| 1 | Charge with a mix: a limit on the **number of workers** and on the **number of jobs each worker can have**, plus AI usage allowances |
| 2 | Payments **manual first, provider-ready**: subscriptions are set by a Console administrator now; when the owner enables a payment provider, it works without redesign |
| 3 | At a limit, **running tasks finish; only new work is blocked** |
| 4 | A **Free plan** that never expires, with the access first proposed for a trial (owner, 16 October, replacing the 14-day trial); contents in section 3 |
| 5 | The Console **pushes** signed entitlement snapshots to AgentGate |
| 6 | Usage resets on **each subscription's own period** |
| 7 | **Same models on every plan**; cheaper plans get lower AI token allowances |

### Rules

1. **Nothing commercial is hard-coded.** Plans, their limits, the starting plan, prices,
   grace periods and the plan new workspaces start on are Console data, changed in the Console.
   AgentGate holds only what the Console sent it. (The existing infrastructure safety ceilings in
   AgentGate's environment settings, such as 50M AI tokens a day, stay as a protection against
   runaway cost; they are not plans.)
2. The plans' contents are decided now (section 3) and entered as Console data, not code.
3. **The CMS link and the signed reads are not touched.** The entitlement channel has its own
   routes, its own signing key and scope, its own settings and its own rate limit. No file of the
   reads (`api/internal/console_reads.py`, `application/console_reads/*`, the reads contract, the
   Console's `signed_reads.py`) or of the CMS changes. The existing verify scripts and contract
   tests prove it in every phase.

## 2. How it works (summary)

```
Console                                         AgentGate
plans + entitlements + subscriptions
        |  builds a versioned snapshot per organisation
        |  signed POST (entitlement channel)  ----------------->  validates, keeps the newest version
        |                                                         counts usage per period
        |  signed GET status  <--------------------------------   applied version + usage this period
        |  nightly: resend any organisation that is behind
```

- **New workspace:** the Console also sends a **new-workspace template** (the Free plan's
  entitlements and usage period; no end date). AgentGate applies it the moment an organisation is
  created, so a workspace can work from sign-up even if the Console is briefly unreachable. The
  Console then sees the new organisation (status read on the entitlement channel), creates its
  billing customer and an `active` Free subscription, and sends the organisation's own snapshot.
  (The template can also describe a trial, `validDays`, should one ever be wanted.)
- **Periods without a newer plan:** when a plan's period ends and the Console has not sent the
  next one (always the case for Free unless the Console renews it), AgentGate starts the next
  period of the same length, so usage still resets. The Console's next snapshot uses the same
  start, so counters carry on.
- **Subscription status drives work:** `trialing`, `active` → limits apply. `past_due` → limits
  apply until the snapshot's `validUntil` (the Console sets the grace). `expired`, `cancelled`,
  `paused`, or a snapshot past `validUntil` → **no new work**: workspaces, results and chat stay
  available, running tasks finish, nothing new starts.
- **No snapshot at all** (only possible if the Console never sent one): no new work, with a
  plain message. Never unlimited.
- **Resets:** a snapshot with a new `periodStart` starts new counters; the old period is kept.

## 3. The plans (entered as Console data)

Proposed values, from the measured usage (median organisation-day 225k AI tokens, busiest 2.25M;
a browser task ~60–120k tokens, a chat message ~14k). All are editable in the Console. **Prices
are the owner's call**, best set after phase 1's cost report gives a real cost per token.

| Entitlement | Free | Starter | Team | Business |
|---|---|---|---|---|
| Length / billing | never expires; usage resets monthly | monthly or annual | monthly or annual | monthly or annual |
| `workers.max` | 2 | 3 | 10 | 30 |
| `jobs.per_worker.max` (standing jobs; one-time tasks do not count) | 3 | 5 | 10 | 25 |
| `tasks.monthly` (every task, however started) | 200 | 1,000 | 5,000 | 20,000 |
| `ai.tokens.monthly` (same models on every plan) | 3,000,000 | 10,000,000 | 40,000,000 | 150,000,000 |
| `members.max` | 2 | 3 | 10 | 30 |
| `connections.max` | 3 | 5 | 15 | 50 |
| `browser.enabled` / `browser.sessions.concurrent` | yes / 1 | yes / 1 | yes / 2 | yes / 4 |
| `coding.enabled` / `coding.seconds.monthly` | yes / 3,600 | yes / 7,200 | yes / 36,000 | yes / 144,000 |
| `events.enabled` (react to GitHub and other events) | yes | yes | yes | yes |
| `schedules.min_interval_minutes` | 60 | 15 | 5 | 1 |
| `web_research.monthly` | 50 | 300 | 1,500 | 6,000 |
| `storage.bytes` | 1 GB | 5 GB | 25 GB | 100 GB |

Free includes every capability with small allowances, and never expires: at a limit, new work
waits for the next period or an upgrade. The entitlement keys are Console entitlement definitions; adding a key later needs an
AgentGate change only to enforce it.

## 4. Phases

### Phase 1: measure better (AgentGate)

- `usage_records` gains `browser_seconds` and `coding_seconds`, filled where browser actions and
  coding sessions already record their time. **Migration** (laptop, Neon).
- A cost report script (read-only): per organisation and period, tasks, AI tokens by model,
  browser and coding seconds, storage. Prices per token are inputs, never constants.
- Proof: tests; a simulator browser task and a coding task show their seconds.

**Built (16 October 2026, branch `feat/commercial-phase1-measure`):**

- Browser time is the time inside each browser action (`PlaywrightBrowserProvider.execute`,
  including a cold Chromium start) and its state check in `verify`. A session left open between
  actions is not counted: it holds memory, not CPU.
- Coding time is the sandbox running time the coding service already adds to
  `coding_sessions.active_seconds`. One listener on that column meters every increase, wherever it
  is made. The idle sweep, which belongs to no task, saves its own `coding.idle` usage record.
- `scripts/cost_report.py` (read-only): per organisation, tasks, chat messages, AI tokens (total
  and by model, from `ai_invocations`), browser and coding seconds, and stored bytes
  (`artifacts`). Prices come only from a `--prices` JSON file; models without a price are named.
- **Database:** migration `0020_usage_tool_seconds` adds `usage_records.browser_seconds` and
  `usage_records.coding_seconds` (float, default 0; adding them does not rewrite the table).
- **Dependencies, environment:** none.

### Phase 2: entitlements and enforcement, switched off (AgentGate)

- **Tables (migration):**
  - `organization_entitlements`: the current snapshot per organisation (version, status, period,
    valid until, entitlements, received at).
  - `entitlement_snapshots`: every snapshot received (history, audit).
  - `entitlement_templates`: the new-workspace template.
  - `organization_usage`: counters per organisation per period (tasks, AI tokens, coding seconds,
    browser seconds, web research, storage).
- **One service** (`app/application/services/entitlements.py`): reads the current snapshot (cached
  per process, invalidated by version), answers "may this organisation do X now?", and records
  usage. Every check goes through it, so no limit logic is scattered.
- **Checks** (section 5), all behind one setting, `ENTITLEMENTS_ENFORCEMENT` (default off). Off:
  nothing changes anywhere. On: the checks apply. **Environment change** (noted per release).
- AI tokens: the existing Redis reservation also counts tokens into the organisation's current
  period (next to the existing daily safety key). The allowance is checked when a new task or chat
  message starts, never in the middle of a running task (decision 3).
- Messages at a limit are plain and say what to do ("Your plan includes 3 workers. Ask your
  administrator to upgrade." until checkout exists).
- Proof: tests for every limit (at, below and above), for each status, for expiry and for "no
  snapshot"; the whole existing suite with the switch off (nothing changes) and on (with a
  generous snapshot, nothing breaks).

**Built (16 October 2026, same branch):**

- One service, `app/application/services/entitlements.py`: reads the plan (cached 30 s per process,
  dropped at once when this process applies a new one), answers every check, counts usage, and
  applies snapshots and templates (`apply_snapshot`, `apply_template`, `start_from_template`) for
  phase 3's channel. A malformed plan (a negative or text limit) is refused, and a limit the plan
  does not name is 0: never unlimited.
- Refusals are HTTP 402 with a plain message ("Your plan includes 10 workers. Ask your
  administrator to upgrade the plan.").
- **Tasks** are checked once, when a task is first admitted (`api/internal/runtime.py`), which
  covers every way a task starts (chat, manual, schedule, event). A task already under way, or
  resumed after an approval, is never checked again. A refused task ends without running and the
  chat is told why. The task is counted on the step's usage record, not by an extra query.
- **AI tokens, browser and coding time, web searches** are counted from the usage record each
  step and chat message already saves; the AI allowance is checked when a task or chat message
  starts. Browser and coding admission (session open, workspace open) check `browser.*` and
  `coding.*` as the existing tool budgets do; a web search checks its allowance.
- **Workers, standing jobs, members, connections, stored bytes, schedule interval** are checked
  where each is created (one-time chat jobs and the built-in web research connection do not
  count; only uploads count towards storage, so a running task's screenshots are never refused).
  A GitHub connection is checked when connecting starts, before the person goes to GitHub.
- **New workspaces** start on the newest template plan (version 0): Free, which never expires;
  a plan whose period ended rolls into the next period of the same length.
- **Efficiency:** the measured work of a task is unchanged; one cached plan lookup per
  organisation is added where usage is saved (benchmark baseline updated: +1 statement per task).
- **Database:** migration `0021_entitlements` (4 new tables). **Environment:**
  `ENTITLEMENTS_ENFORCEMENT` (default `false`). **Dependencies:** none.

### Phase 3: the entitlement channel (AgentGate and Console)

**AgentGate** (new router `api/internal/console_entitlements.py`, prefix
`/internal/console/entitlements/v1`, off by default):

- `POST /snapshots`: apply one organisation's snapshot. Signed with its own key
  (`CONSOLE_ENTITLEMENTS_KEY`, scope `entitlements.write`), timestamp and replay protection.
  Validates the contract, rejects an older or equal version (answers "already applied" for the
  same one), applies in one transaction, answers with the applied version.
- `POST /template`: replace the new-workspace template (versioned the same way).
- `GET /status`: applied version, status, period and usage this period, for organisations (paged),
  so the Console can confirm delivery and show usage, and can see new organisations.
- The contract: `docs/integration/console-entitlements/contract.v1.json` (JSON Schema), shared by
  both sides' tests.

**Console:**

- Builds a snapshot whenever a subscription, its plan or a plan's entitlements change (an outbox:
  saved with the change, sent after commit, retried with backoff).
- Versions only go up per organisation; the last delivered and confirmed version is shown on the
  subscription.
- A nightly reconcile resends any organisation whose applied version is behind, and creates the
  billing customer and an `active` Free subscription for new organisations.
- Settings `AGENTGATE_ENTITLEMENTS_*` (URL, key, timeout), separate from `AGENTGATE_READS_*`.
- **Migration** in the Console database (outbox, delivered versions).

Proof: contract tests on both sides; the existing reads and CMS tests and verify scripts pass
unchanged; both systems run locally together, with a lost delivery recovered by the reconcile.

**Built (16 October 2026):**

- **AgentGate** (`feat/commercial-phase1-measure`): `app/application/console_entitlements.py`
  (settings, `audoryn-entitlements-v1` signing, its own replay and rate records in Redis) and the
  router `app/api/internal/console_entitlements.py`. Shared primitives (key type, rejection errors,
  secret decoding) are imported from the reads; no reads file changed. Contract, signing vector
  and README in `docs/integration/console-entitlements/`.
- **Console** (`feat/commercial-entitlements`, from `main`): commercial settings (the plan new
  workspaces start on, usage period, optional trial, past-due grace) and the per-organisation
  outbox (migration `0009_agentgate_entitlements`); `EntitlementDeliveryService` builds, stages,
  sends and reconciles; `CommercialService` stages on subscription, plan and entitlement changes;
  an in-process worker (like the CMS distribution) sends within seconds and reconciles every
  `AGENTGATE_ENTITLEMENTS_RECONCILE_MINUTES`; API `GET/PUT /commercial/settings` and
  `GET /commercial/entitlement-deliveries`. Text entitlements stay in the Console.
- **Simplified:** the Console does not create a customer and subscription for every new
  workspace. AgentGate starts each new workspace on the Free template, which never expires; the
  Console sends a workspace's own plan once an administrator sets its subscription.

### Phase 4: cutover

- Enter the entitlement definitions and the four plans (section 3) in the Console, through its
  existing plan screens.
- Send the template, then a snapshot for every existing organisation (a subscription for each,
  set by an administrator).
- Switch `ENTITLEMENTS_ENFORCEMENT` on.
- Remove AgentGate's hard-coded `PLANS` (`api/product_ops_routes.py`). "Usage & billing" shows the
  delivered plan, its limits, the period and real usage, with warnings at 80% and 100%. Its
  page-view loading of up to 1,000 runs and actions is replaced by the usage counters.
- Proof: simulator run of a new workspace (sign-up → Free → each limit → next period → upgrade →
  higher limits; a cancelled plan → no new work); Oracle handoff for both systems.

**Built (16 October 2026, both branches):**

- **Existing workspaces:** when AgentGate receives a template, every workspace that has no plan yet
  starts on it (version 0), so switching enforcement on never leaves a workspace without a plan.
- **Plan name:** the snapshot and the template carry the Console plan's name (`planName`, optional
  in contract v1), so AgentGate shows the Console's name, never one of its own.
- **AgentGate:** the hard-coded `PLANS` are gone. `/commercial` returns the delivered plan, its
  period, every limit with its use (`ok`, `near` at 80%, `reached` at 100%) from the usage
  counters, what is included, and health counts; `/product` keeps only the workspace and setup.
  The page no longer loads up to 1,000 runs and 1,000 actions per view.
- **"Usage & billing" page:** the plan, the usage period, warnings at 80% and 100%, this period's
  allowances, the workspace's counts, what is included, and how to change plan (ask the
  administrator until checkout exists).
- **Console:** `python -m app.bootstrap.entitlement_delivery` stages every subscribed
  organisation's plan and the template (the cutover; safe to repeat).
- **Switches:** `ENTITLEMENTS_ENFORCEMENT=true` in the simulator; Oracle at the deploy, after the
  plans are delivered. The signed Console reads are switched on with a new key pair.
- **Dependencies, database:** none new.

From here plans can be sold manually: a Console administrator sets the subscription and the
customer's limits follow within seconds.

### Phase 5: payment provider, ready but off (Console, small AgentGate part)

**Decided and built (16 October 2026):** Paystack (naira) and Paddle (other currencies) side by side;
see `docs/plans/commercial-payments-plan.md`.

- An adapter for the provider the owner chooses then, implementing the existing `BillingProvider`
  interface: checkout, renewal, failed payment (`past_due` → grace → `expired`), cancellation,
  webhooks (verified, de-duplicated by the existing unique keys), refunds into the ledger.
- Every outcome becomes a subscription change, so the snapshot follows automatically.
- AgentGate's "Upgrade" asks the Console for a checkout link through a narrow signed command
  (new; approved with this phase). Until a provider is enabled, it shows "contact us".
- Off until the owner enables it in the Console.

### Phase 6: administrative commands (later, separately approved)

Manual usage reset, top-up, temporary extension: scoped, audited commands under Decision 0001's
command rules.

## 5. Where AgentGate checks

| Entitlement | Checked at | Points |
|---|---|---|
| status (no new work) | everywhere new work starts (the points below) | – |
| `workers.max` | `create_worker` (`api/workforce_routes.py`) | 1 |
| `jobs.per_worker.max` | job creation (`api/jobs_routes.py`); one-time (`oneShot`) jobs do not count | 1 |
| `tasks.monthly` | `queue_job`, `integration_events`, `integration_tasks`, schedule dispatch | 4 |
| `ai.tokens.monthly` | when a new task or a new chat message starts; usage is counted at the AI budget reservation (`infrastructure/ai/workloads.py`) | 2 |
| `members.max` | organisation creation, invitation and join | 2 |
| `connections.max` | `integration_capability_routes`, `browser_origin_authority`, `github_connector`, `worker_autonomy` | 4 |
| `browser.*`, `coding.*`, `events.enabled` | the capability catalogue at task preparation (excluded with "Not included in your plan"), and the existing browser and coding admission | 2 |
| `schedules.min_interval_minutes` | schedule creation and update | 1 |
| `web_research.monthly` | the existing web research budget | 1 |
| `storage.bytes` | artifact and attachment creation | 2 |
| new workspace → template | organisation creation (`organization_routes`, `repositories`) | 2 |

A running task is never stopped by a limit (decision 3): every check happens only when new work
starts. The AI allowance is checked when a new task or a new chat message starts; a task already
running finishes even if it passes the allowance, and the extra is recorded and shown as usage
over the limit. The environment's safety ceiling (tokens per day) still protects against a
runaway task, as today.

## 6. Across all phases

- Each phase: tests, then the simulator, then an Oracle handoff; findings in
  `docs/testing/simulation-findings.md`.
- Migrations run on Neon from the laptop; every dependency, environment and database change is
  listed in the release notes and the handoff.
- No new dependency is expected; if one is needed, the owner is asked first.

## 7. Open points (not blocking phase 1)

- Prices for each plan (after phase 1's cost report).
- The payment provider (at phase 5).
- Whether an administrator can give a Free workspace extra allowance for a period (phase 6 command).
