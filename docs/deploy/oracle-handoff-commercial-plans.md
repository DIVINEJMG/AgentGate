# Oracle handoff: commercial plans (in progress)

Started 16 October 2026 on branch `feat/commercial-phase1-measure`. Updated as each phase lands;
committed, pushed and deployed only when the whole piece of work is done. Plan:
`docs/plans/commercial-plans-plan.md`.

It follows *Audoryn Oracle Update and Deployment Rules — Illustrated Edition*: inspect first,
change only what the release needs, and verify the outcome.

## 1. Changes so far

### Phase 1: measure (built, tested)

- Each usage record now also saves **browser seconds** (time inside each browser action and its
  state check) and **coding seconds** (sandbox running time, metered wherever it is added; the idle
  sweep saves its own `coding.idle` record).
- `backend/scripts/cost_report.py`: read-only usage and cost per organisation. Prices come only
  from a `--prices` JSON file.
- Execution is unchanged: measuring never blocks or alters a task, and a failed save of a usage
  record is logged and dropped as before.

### Phase 2: plan limits, switched off (built, tested)

- One service enforces the plan the Console sends: status, workers, standing jobs per worker,
  tasks, AI tokens, members, connections, browser and coding, events, schedule interval, web
  searches and storage. Only new work is refused; a running task always finishes.
- **Off by default** (`ENTITLEMENTS_ENFORCEMENT=false`): no check reads or refuses anything.
  Usage is counted only for organisations that have a plan, and none has one until phase 3.
- New workspaces start on the Console's template plan once one exists (phase 3 sends it): Free,
  which never expires; its usage resets each period.

### Phase 3: the entitlement channel (built, tested; off on both sides)

- **AgentGate:** new routes under `/internal/console/entitlements/v1` (apply a snapshot, apply the
  template, read status), signed with their own key. Off unless `CONSOLE_ENTITLEMENTS_ENABLED`.
- **Console:** commercial settings and a delivery outbox; a worker sends plans and reconciles. Off
  unless `AGENTGATE_ENTITLEMENTS_ENABLED`. New API: commercial settings and delivery status.
- Contract, signing and settings: `docs/integration/console-entitlements/README.md`.

### Phase 4: cutover (built, tested; enforcement on in the simulator)

- **AgentGate:** a delivered template also starts every workspace without a plan on it. The
  hard-coded plans are removed; "Usage & billing" shows the plan the Console sent, its period, each
  limit with its use, and warnings at 80% and 100%. The page reads the usage counters instead of
  loading up to 1,000 runs and actions.
- **Console:** snapshots and the template carry the plan's name; a cutover command stages every
  subscribed organisation's plan.
- **Signed reads:** switched on (laptop) with a new key pair; no reads code changed.

### Phase 5: payments, ready but off (built, tested with the providers' answers mocked)

- **Owner's decision:** Paystack for naira, Paddle (merchant of record) for other currencies, side
  by side. Design and security: `docs/plans/commercial-payments-plan.md`; channel and dashboard
  settings: `docs/integration/console-billing/README.md`.
- **Console:** prices per plan in more currencies; payment provider switches (which currency each
  serves); checkouts; the providers' signed webhooks, each confirmed through the provider's API
  before a subscription changes; a paid plan that ends returns the workspace to Free. Admin
  screens: Plans (prices in other currencies) and Payment providers (switches and checkouts).
- **AgentGate:** "Usage & billing" shows the Console's plans and prices, starts a checkout (only
  people who manage the workspace), confirms the payment on return, opens Paddle's checkout on its
  own page, and links to the provider's management page.
- Off until provider keys are set on the Console **and** an administrator switches a provider on.

### Plans page and upgrade prompts (built, tested; `docs/plans/plans-news-upgrade-plan.md`)

- **Plans page** `/app/plans`: plans, prices (monthly/annual, currency), comparison, checkout and
  "Manage billing"; "Usage & billing" keeps usage and links to it. Paystack returns to
  `/app/plans?checkout=<id>`; Paddle's checkout opens on `/app/plans/pay/<id>`.
- **Upgrade prompts:** a banner for a limit at 80% or more (one per person, away for a week after
  a dismissal), a modal when the plan refuses new work (402), "Available on <plan>" on the Browser
  and GitHub tool pages when the plan leaves the feature out, and "Ask an admin to upgrade" for
  members who cannot buy (an audit record `commercial.upgrade.requested`, at most once a day per
  workspace and limit; administrators see it as a banner). Routes (v1 and v2):
  `GET /organizations/{id}/plan/prompts`, `POST .../plan/prompts/dismiss`,
  `POST .../plan/upgrade-requests`. A 402 now names the limit in the `X-Plan-Limit` header, which
  CORS exposes; the message is unchanged.

### What's new and the Console screens (built, tested)

- **Workspace app:** a bell with the unread count and a "What's new" panel: CMS changelog entries
  (website server route `/_public/changelog`, from the verified release; no CMS change) and the
  Console's announcements for the workspace app (filtered by plan and role); a Major announcement
  opens once per person. AgentGate routes (v1 and v2): `GET /organizations/{id}/news`,
  `POST .../news/state`.
- **Channel:** new scope `content.read` on the billing channel (`GET
  /internal/agentgate/billing/v1/content`); both sides already know it in code, so the existing
  billing key pair needs no change.
- **Console:** Plans page text per plan (tagline, highlights, badge; admin `GET`/`PUT
  /commercial/plans/{id}/page`, audited) and workspace-app options on announcements (in the
  existing `audience` field, now validated). `generated.ts` regenerated.

## 2. Dependencies, environment and database

| Kind | Change | Where applied |
|---|---|---|
| Dependencies | none (both providers are called with the existing `httpx`; Paddle.js is loaded by the browser from `cdn.paddle.com` on the payment page only) | – |
| Environment | `ENTITLEMENTS_ENFORCEMENT` (new, default `false`). Simulator (`.env.sim`): `true` since phase 4. Oracle: add `ENTITLEMENTS_ENFORCEMENT=true` **only after** every workspace has a plan (section 3, step 4) | Simulator only |
| Environment | Signed reads switched on: AgentGate `CONSOLE_READS_ENABLED=true`, Console `AGENTGATE_READS_ENABLED=true`, with a new key pair `console-reads-2026-10` (`CONSOLE_READS_KEY_ID`/`_SECRET`, `AGENTGATE_READS_KEY_ID`/`_SECRET`; scopes unchanged) replacing the local test key | Laptop `.env` files of both, 16 Oct. Oracle and the Console server get the same pair at the deploy |
| Environment | AgentGate `CONSOLE_ENTITLEMENTS_*` (new, default off); Console `AGENTGATE_ENTITLEMENTS_*` (new, default off). A new key pair is generated at the cutover; not shared with the reads | – |
| Database (Console) | migration `0009_agentgate_entitlements`: `console.commercial_settings` (one record) and `console.entitlement_deliveries` | **Applied 16 Oct** to the Console database `ep-shiny-cake` from the laptop (owner's approval) |
| Data (Console) | 14 entitlement definitions; plans Free, Starter, Team, Business with the plan's limits (prices 0 until set); settings: new workspaces on Free, 30-day period, no trial, 7 days' grace; a Team subscription for workspace `650c94c4`. Entered through `CommercialService`, audited as the owner | **Entered 16 Oct** (owner's approval) |
| Keys | A new key pair `console-entitlements-2026-10` in the laptop's AgentGate `backend/.env` (`CONSOLE_ENTITLEMENTS_*`, enabled) and Console `backend/.env` (`AGENTGATE_ENTITLEMENTS_*`, enabled, origin `http://localhost:8000`) | Laptop only. Oracle's `/etc/audoryn/audoryn.env` and the Console's server environment get it at the deploy (the Console's origin then points at AgentGate's internal address) |
| Database | migration `0020_usage_tool_seconds`: `usage_records.browser_seconds`, `usage_records.coding_seconds` (float, default 0; no table rewrite) | sim branch (`ep-twilight-surf`): applied 16 Oct. **Production (`ep-long-glitter`): not yet**; apply from the laptop before the deploy, with approval |
| Database | migration `0021_entitlements`: new tables `organization_entitlements`, `entitlement_snapshots`, `entitlement_templates`, `organization_usage` (no change to existing tables) | sim branch: applied 16 Oct. **Production: not yet** (same step as 0020) |
| Contract | `planName` added to the snapshot and the template (optional; contract v1). Deploy AgentGate before the Console: an older AgentGate refuses the field | – |
| Database (Console) | migration `0010_payments`: `console.billing_plan_prices`, `console.billing_provider_prices`, `console.billing_checkouts`; `commercial_settings.payment_providers`; `billing_subscriptions.provider_synced_at`; the administrator columns of customers, subscriptions, subscription changes, payments, invoices and refunds become nullable (empty = recorded from the provider) | **Applied 16 Oct** to the Console database `ep-shiny-cake` from the laptop (owner's approval); existing 4 plans and 1 subscription unchanged |
| Environment (Console) | `AGENTGATE_BILLING_ENABLED`, `_KEY_ID`, `_KEY_SECRET`, `_AUDIENCE`; `AGENTGATE_WEB_ORIGIN`; provider keys `PAYSTACK_SECRET_KEY`, `PAYSTACK_MODE`, `PAYSTACK_WEBHOOK_IPS`, `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_CLIENT_TOKEN`, `PADDLE_ENVIRONMENT`, `PAYMENTS_TIMEOUT_SECONDS` (all new, off by default). Test keys are refused in production | Laptop `.env`: billing key pair `agentgate-billing-2026-10` and `AGENTGATE_WEB_ORIGIN=http://localhost:5173`, 16 Oct; the owner then set the Paystack test key and the Paddle Sandbox API key, webhook secret and client token (test mode) |
| Environment (AgentGate) | `CONSOLE_BILLING_ENABLED`, `_ORIGIN`, `_KEY_ID`, `_KEY_SECRET`, `_AUDIENCE`, `_TIMEOUT_SECONDS` (new, off by default) | Laptop `.env`: same pair, origin `http://localhost:8001`, 16 Oct |
| Database | migration `0022_user_content_state`: new table `user_content_state` (person, item key, read/dismissed times), for dismissed prompts now and read news later (no change to existing tables) | sim branch (`ep-twilight-surf`): applied 16 Oct (owner's approval). **Production: not yet**; with 0020 and 0021 before the deploy |
| Database (Console) | migration `0011_plan_pages`: new table `console.billing_plan_pages` (plan, tagline, highlights, badge); no change to existing tables | **Applied 17 Oct** to the Console database `ep-shiny-cake` from the laptop (owner's approval) |
| Contract | New channel `audoryn-billing-v1` (AgentGate -> Console) with its own shared signing vector | – |
| Data (simulation) | template 2 (Free, with its name) sent to the simulator: its 3 workspaces without a plan started on Free. A test workspace `007f7a51` (Phase 4 sim workspace) with a test user. The Console database was only read | Simulator only |

## 3. Cutover order

1. Migrations `0020`, `0021` and `0022` on production (below), from the laptop, with approval.
2. Deploy AgentGate with `CONSOLE_ENTITLEMENTS_*` and `CONSOLE_READS_*` set and enabled, and
   `ENTITLEMENTS_ENFORCEMENT` **absent** (off).
3. Deploy the Console with `AGENTGATE_ENTITLEMENTS_*` (origin: AgentGate's internal address) and
   `AGENTGATE_READS_*`. Then, with approval, stage every plan from the Console backend:
   `python -m app.bootstrap.entitlement_delivery`. Its worker sends the template (every existing
   workspace starts on Free) and each subscription's plan within seconds.
4. Check that every workspace has a plan: the Console's `GET /commercial/entitlement-deliveries`
   shows `delivered`, and AgentGate's `organization_entitlements` has one row per organisation
   (`select count(*) from organizations` equals `select count(*) from organization_entitlements`).
5. Only then add `ENTITLEMENTS_ENFORCEMENT=true` to `/etc/audoryn/audoryn.env` and restart.

Without step 4 a workspace with no plan would get "This workspace has no plan yet" for new work.

## 4. Before the deploy (laptop)

Apply the migration to the production Neon database from the laptop (never on Oracle), after
approval:

```bash
cd backend && .venv/Scripts/python.exe -m alembic current && .venv/Scripts/python.exe -m alembic upgrade head && .venv/Scripts/python.exe -m alembic current
```

- **Expected:** `0019_usage_prompt_chars`, then `0022_user_content_state (head)` (0020, 0021 and
  0022 applied).
- Deploying the code before this is safe but measures nothing: usage records fail to save (logged)
  until the columns exist.

## 5. Proof so far

- **Tests:** 1,744 backend tests pass (6 new: meter, browser timing even when an action fails,
  coding time on real `CodingSession` rows, the idle sweep's record, saving, the cost report with
  and without prices).
- **Static checks:** ruff, format, pyright, the changed-code guard, every verify script and the
  endpoint audit pass.
- **Simulator:** a browser task ("Open …/test-site/workers and tell me which workers are listed")
  completed normally and its execute step was saved with `browser_seconds = 10.485` (cold Chromium
  start, page load and settle). The cost report read it back.
- **Simulator, coding:** a read-only coding task on `DIVINEJMG/school` (read the branch, open an
  E2B workspace, read README.md, close it) completed normally. Its steps saved 66 + 92 + 84 =
  242 coding seconds, exactly the 242 s of running time on the coding session.

- **Phase 2 tests:** 1,767 backend tests pass (23 new for plan limits: every limit at, below and
  above; each status; expiry; no plan; versions; template; a refused task; a task under way never
  stopped; one-time jobs; the chat check; browser concurrency). The runtime benchmark shows the
  measured work of a task unchanged.
- **Phase 2 simulator (enforcement switched on in the simulator only):** with a Team plan a browser
  task ran and was counted (1 task, 7 browser seconds); with the task allowance used up, a new task
  ended without running and the chat said "This period's tasks (1) are used up. It renews on 15
  November 2026. Ask your administrator to upgrade the plan."; with the plan expired, the chat said
  "This workspace's plan is expired, so new work can't start. …"; renewed, work ran again and the
  refused tasks were not counted (2 tasks). The simulator was then switched back to off.
- **CMS link and signed reads:** no file of either changed.
- **Phase 3 end to end (both systems on the laptop, real Console database, simulation AgentGate):**
  the Console's worker read AgentGate's status with the new key (200); the Free template was
  delivered (AgentGate holds template 1, no expiry); the Team subscription reached AgentGate as
  version 5; after the workspace's plan was deleted in AgentGate, the Console's reconcile resent it
  and it was applied again. Two defects found and fixed on the way (F53, F54). Final: AgentGate
  1,777 tests, Console 350 tests.
- **Phase 3 tests:** AgentGate 1,775 pass (7 new: shared signing vector; configuration; every
  signature refusal, including a read signature on the write channel; versions; template; paged
  status; refusals never reach the database; rate limit; Redis down; channel off). Console 348 pass
  (13 new: the same vector, contract fields, valid-until per status, staging only on change,
  retry and give-up, template, reconcile, settings validation and audit, the signed client). The
  Console's lint and type-check lists, the generated API client, migration heads, the secret scan
  and the readiness gate pass; its frontend type-check shows only stale `.next` files from another
  branch (no source errors).
- **Phase 4 tests:** AgentGate 1,779 (new: existing workspaces start on the first template; the
  usage page's plan, period and warnings from the counters). One test,
  `test_console_reads_foundation.py::test_default_settings_keep_every_route_hidden`, fails on the
  laptop only because the laptop's `.env` now switches the reads on; with them off it and the
  other 46 reads tests pass, and CI has no `.env` (F56). Pyright 0 errors (46 test-fake errors
  from phase 2 fixed, F55). Console 351 pass with the reads off (new: the cutover stages each
  subscribed organisation once); one Console reads test has the same laptop-only cause (F56). Frontend type-check and 158 tests pass.
- **Phase 4 simulator (enforcement on):**
  - cutover: template 2 sent over the signed channel; the 3 workspaces without a plan started on
    Free (version 0), the owner workspace kept Team (version 5);
  - a new sign-up workspace started on Free (Console limits, named "Free");
  - workers: 2 created, the 3rd refused: "Your plan includes 2 workers. Ask your administrator to
    upgrade the plan.";
  - AI tokens at 2.5M of 3M showed `near`; at 3M a chat message was refused before any work:
    "This period's AI tokens (3,000,000) are used up. It renews on 15 November 2026. …";
  - a month later (period moved back 31 days) the next period started with 0 used;
  - upgrade to Team (version 1): applied at once, the 3rd worker created, 3 of 10;
  - cancelled (version 2): new workers and chat refused ("This workspace's plan is cancelled, so
    new work can't start. …"); workers, conversations and settings still readable.
- **Phase 5 tests:** Console 378 (27 new: both providers' signatures over the raw body, a stale or
  forged Paddle signature, a rotated Paddle secret, Paystack's optional IP allowlist; payments
  confirmed through each provider's API; a foreign checkout host refused; outages retryable and
  never echoing provider bodies; the signed channel (wrong scope, changed body, late, wrong
  audience, replay) and the shared vector; test/live keys against the environment; the catalogue;
  the Console's price and one provider price per Console price; a retried request; refusals (not
  offered, other currency, invalid, rate limit, provider switched off, a second paid
  subscription); a confirmed payment applied once; a price, amount, currency or checkout mismatch
  changing nothing; an unsigned webhook not stored; past due, renewal, cancellation at period end,
  an older event ignored, the end of a paid plan back to Free; a refund; provider switches and
  extra prices validated and audited). AgentGate (new: the shared vector, the channel only when
  safely configured, signed requests and checked answers including a refused redirect, only
  managers buy with their own email and only to the provider's page, the page sends only a plan,
  cycle and currency).
- **Phase 5 sandbox run (16 Oct; laptop Console on the production Console database, simulation
  AgentGate, a tunnel for the providers' webhooks):** test prices on Team (USD 49, NGN 40,000 a
  month; owner to set real prices), Team made purchasable, Paystack (NGN) and Paddle (USD) switched
  on, all through the audited Console services.
  - **Paystack:** a new workspace chose Team in NGN; Paystack's test checkout (Success); the
    signed webhook (from Paystack's published address) confirmed through Paystack's API; payment
    NGN 40,000 recorded, subscription Team active with Paystack's subscription linked, the plan
    delivered to AgentGate (Team, 10 workers).
  - **Paddle:** a second workspace chose Team in USD on "Usage & billing"; the checkout opened on
    Audoryn's own page with Paddle.js (USD 49.00 incl. Paddle's VAT); paid with Paddle's sandbox
    card; webhooks confirmed through Paddle's API; one payment of USD 49.00 (two payment events,
    one record); Team delivered to AgentGate. A replayed event changed nothing; a cancellation at
    period end kept Team and set "ends at period end"; an immediate cancellation returned the
    workspace to Free (AgentGate plan version 3).
  - Unsigned and wrongly signed webhooks were refused with 401 and stored nothing.
  - Findings F58-F62 fixed on the way. Console 380 tests pass.
- **Signed reads (switched on):** the Console's reads client read overview, health and
  organisations from the simulator with the new key.
- **Page:** "Usage & billing" checked in the workspace preview (warnings, meters, period, included).
- **Upgrade prompts:** 1,790 backend tests (1,789 pass; the one failure is F56, which passes with reads off), 158 frontend tests, pyright 0, parity and UI-exposure checks pass; 5 new tests (banner choice and the week after a dismissal, nothing while
  enforcement is off, requests once a day per limit and seen by administrators, unknown keys
  refused, the limit named on a refusal). Checked in the workspace preview at desktop and phone
  widths: banners, the modal with the meter and the plans that give more, "Available on Starter"
  on the GitHub page, dismissal. Against the simulator: the prompt state for the Paddle test
  workspace (Free) and the `X-Plan-Limit` header exposed to `http://localhost:5173`. A worker
  creation started there to reach a real 402 waited on the AI planner and was abandoned (one test
  conversation left in that workspace).
- **What's new and Console screens:** AgentGate 1,794 backend tests pass (reads off for F56), 159 frontend tests; 4 new backend tests (announcement checks and safe links,
  plan and role filtering, the 60 s cache and the last good answer, read state per person and
  only for news keys) and 1 website loader test (verified changelog entries, newest first, a
  tampered page left out); Console 4 new tests (Plans page text validated, audited and in the
  catalogue; the content scope; audience checks; only active in-app announcements in their
  window). Console 384 tests (383 pass; the other is F56, which passes with reads off). Checked
  in the workspace preview at desktop and phone widths: the Major window, the bell count, the
  panel (tags, "New to you", Offer with "See plans", dismiss). Live: the simulator's news route
  read the Console's announcements over the signed channel (200; an unsigned request 401) and the
  catalogue carried the new fields. No Console data was written (no plan text or announcement
  was created).

## 6. Live checks after the deploy

| # | What to do | Expected |
|---|---|---|
| 1 | Any browser task | Its execute steps have `browser_seconds` > 0 |
| 2 | Any coding task | Its steps, or a later `coding.idle` record, have `coding_seconds` > 0 |
| 3 | `python scripts/cost_report.py --since <today>` in the container | One line per organisation with tasks, tokens, browser and coding seconds |
| 4 | Before step 5: any chat task, schedule, worker or connection change | Works exactly as before (enforcement is off) |
| 5 | After step 5: "Usage & billing" in a workspace | The Console's plan name, the period, each limit with its use; the owner workspace shows Team |
| 6 | After step 5: a chat task in any workspace | Runs as before (all workspaces are within their plans) |
| 7 | Console administrator pages that read AgentGate (overview, organisations, health) | Data, not "unavailable" |

## 7. Oracle commands

Step by step, for each VM: `docs/deploy/oracle-commands-commercial-plans.md` (17 October 2026).

## Announcement media (branches `feat/announcement-media` in both repos, 17 October 2026)

What changed:
- **Console migration `0012_announcement_media`:** adds a nullable `media` jsonb column to
  `console.public_announcements`. It is run from the laptop against the Console database (Neon),
  never on Oracle. **Applied from the laptop on 17 October 2026** (`0011_plan_pages` → `0012_announcement_media`).
- **Console API:** `PUT /api/v1/announcements/{id}/media` (permission `platform.announcements.manage`)
  takes verified CMS assets (an image, plus an optional dark-mode image and MP4/WebM video) and alt
  text. It copies them to public storage under the same immutable keys a site release uses
  (`cms-public/assets/<sha256>/<id>`), checks each one is reachable, and stores the addresses.
  Sending no image clears the media. The CMS and its releases are unchanged.
- **Console content feed:** each announcement now carries `media`.
- **AgentGate:** `news._media` keeps media only from one HTTPS origin and the published asset keys
  (a storage path in front of them, such as the bucket, is allowed).
  The window now has 16:9 media on top, with the app's own cover when there is no media. Several
  major announcements share one window, with pages, dots, Back and Next.

Not changed:
- No new dependencies and no environment changes.
- Uploading media needs the Console's existing public storage settings (`CMS_PUBLIC_ORIGIN`,
  `CMS_PUBLIC_STORAGE_BUCKET`); without them the media route answers 409.

Deploy: migrate `0012`, then the Console (Oracle blocks A–F, then Vercel `audoryn-console`), then
AgentGate (Oracle blocks A–F, then Vercel `audoryn`).
- **Later the same day (UI only):** the media fields moved into the announcement form itself
  (New and Edit), and one Save publishes them. The Plans page's large banner was replaced by the
  standard page header, with the Monthly/Annual and currency controls on its right.
- **Worker branch names (branch `fix/announcement-media-form`):** new GitHub branches start with
  `audoryn/<task>/` instead of `codex/<task>/`. The prefix stays fixed in AgentGate, not set in the
  Console, so running work never depends on the Console. A task can still write to its own older
  `codex/` branches. The conversation page also keeps the plan banner compact.

## Usage resets (branch `feat/usage-reset` in both repos, 17 October 2026)

- **AgentGate migration `0023_usage_resets`:** a new `usage_resets` table. **Applied from the laptop on 17 October 2026 to the sim branch (`ep-twilight-surf`) and the Oracle database (`ep-long-glitter`).** It is run from the
  laptop (sim database, then Neon), never on Oracle, and only after approval.
- **AgentGate:**
  - `POST /internal/console/entitlements/v1/usage-resets` on the existing entitlements channel
    (scope `entitlements.write`). It takes `grantId`, `mode` (`now`/`banked`), `target`
    (`everyone` / `organizationIds` / `planCodes`), and optional `expiresAt` and `note`.
  - A repeated `grantId` changes nothing.
  - `now` zeroes the current period's usage counters. Plans, periods, workers and members are
    never changed.
  - Workspace route `POST /organizations/{id}/plan/usage-resets/use` (`organizations.manage`)
    uses the banked reset that expires soonest.
  - The usage summary now carries `resets`, so Settings → Usage & billing shows "Use a reset".
  - Every reset is audited as `commercial.usage.reset`, with the usage it cleared.
- **Console:**
  - `POST /api/v1/commercial/usage-resets` (`billing.subscriptions.manage`), audited as
    `commercial.usage.reset`.
  - A "Give a usage reset" control on Commercial → Subscriptions.
- **Also on this branch:** the `audoryn/` worker branch prefix and the compact plan banner on
  conversation pages (both described above).
- **Not changed:** no new dependencies, keys, scopes or settings.
- **Deploy:** migrate `0023`, then AgentGate (Oracle A–F, then Vercel `audoryn`), then the
  Console (Oracle A–F, then Vercel `audoryn-console`). AgentGate goes first so the Console's
  call finds the route.
