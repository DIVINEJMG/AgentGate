# Database transfer during local development

Neon public network transfer includes results returned to a local backend. AI token
budgets do not control this allowance. Review project usage and its reset date in
the Neon Console; code inspection alone cannot attribute monthly usage to queries.

## Progress delivery

- Visible active conversations refresh task activity every 10 seconds.
- Conversations with no active task refresh activity every 60 seconds.
- Realtime events refresh activity immediately; the feed owns the sole polling timer.
- Hidden tabs pause activity polling and refresh on visibility restoration.
- Conversation and approval fallback polling runs every 30 and 60 seconds respectively.
  WebSocket/SSE events retain immediate delivery. Hidden fallback polls are skipped;
  returning to the tab refreshes current state.
- Activity SQL selects presentation fields: scope, path, changed paths, references,
  verification, status and timestamps. File bodies, prompts, planner proposals and
  workspace evidence remain in their canonical records, available to execution.
- Activity fetches only the latest run per task and the latest planning decision
  and attempt for its current display. Preparation reads exclude saved request
  context; only the current decision pointer is needed. Audit history is retained.
- Read authorization and live ownership checks still run on each activity refresh.

No migration or Neon setting change is required. Restart the backend and refresh
the frontend after installing this change. Stopping local development processes
when they are unused also avoids background recovery and queue queries.

## Efficiency pass (12 October 2026)

The staging project used 5.56 GB of transfer in October for a database of about 47 MB, so
the free plan suspended it. Code review found three multipliers; all three are fixed with
the same responses.

- **Workspace live reloads.** `workspace/live.tsx` reloaded workers, runs, approvals and
  escalations on every live event, at most every 0.4 s; during a run that was 2–3 full
  reloads a second per open workspace. Events now reload only the lists they can change
  (`workspace/liveLists.ts`), batched for 1 s. Fallback polling and page actions still
  reload everything.
- **Runs list** (`GET …/runtime`, and every single-run response). Each of up to 200 runs
  read its work item, job and worker rows, and every step and artifact row (including step
  input and output JSON) to return their IDs. `_runs_public` now loads any number of runs in
  five queries: IDs only for steps and artifacts, and only the `runtime` part of the work
  item payload.
- **Approvals list** (`GET …/approvals`, and every single-approval response). Each of up to
  100 approvals read about six rows, including the whole work item payload and job revision
  definition, to find its conversation thread. `_approvals_public` and
  `approval_conversation_ids` load them in at most five queries, reading only the
  `integrationOrigin` and `autonomy` JSON parts.

Tests: `backend/tests/test_efficiency_queries.py` keeps the previous per-row code as the
reference. It checks identical records, a fixed number of queries at any size, no step input
or output reads, and that the new queries compile for PostgreSQL. `frontend/tests/liveLists.test.mjs`
covers which lists each event reloads.

Fixes 4 and 5, the same day:

- **Policies, actions, incidents, memories and results lists** now load in at most three
  queries each, with the same records.
  - **Incidents and memories:** each row loaded every incident or memory audit event of its
    organisation, as whole rows. Now one query reads only the events about the listed rows,
    and only their `metadata` part.
  - **Results:** up to 300 rows each loaded their worker, job and latest version separately.
  - Single-record responses use the same code.
- **Preparation recovery** runs every 15 s only while preparations are active or were just
  started.
  - When nothing is pending it waits `PREPARATION_RECOVERY_IDLE_SECONDS` (default 600), or
    until the earliest scheduled retry.
  - A finished preparation wakes it.
  - So an idle backend lets the Neon compute suspend after five minutes, instead of querying
    every 15 s forever.
- **Tests:** `backend/tests/test_efficiency_lists.py`. Five policy tests now stub
  `_policies_public`, the batched loader, instead of `_policy_public`.

## Backup database and building a fresh one (12 October 2026)

- **Local AgentGate now uses a backup database:** Neon project `Audoryn-staging-Backup`
  (`ep-long-glitter`, branch `production`, database `neondb`). The previous `DATABASE_URL`
  (project `audoryn-staging`, `ep-small-glade`) stays in `backend/.env` as a comment with
  switch-back instructions. The previous project is suspended until 1 November 2026 and its
  data is kept.
- **Migrations alone cannot build a new database.** Migration 0001 creates every table from
  the current models, and later migrations create some of them again.
  - `backend/scripts/create_fresh_database.py` builds an empty database the way 0001 does,
    then stamps the latest migration. It refuses to run on a database that has any table.
    Run it once without `--apply` to check.
  - The models now also declare the two indexes that existed only in migrations:
    `uq_integration_command_message` (one integration command per chat message) and
    `ix_outbox_delayed`.
  - `tests/test_fresh_database_schema.py` fails if a migration creates a named index or
    constraint that the models do not declare.

## Verification and remaining measurements

Regression tests cover selected SQL expressions, tenant/resource visibility,
historical progress, failed refresh recovery, overlapping requests, idle cadence
and visibility restoration. These checks use no production database.

Compare the Console's transfer growth over equivalent usage windows to measure
savings. Other execution/context/history queries still require data to perform
their features; their contribution needs measurement before further changes.
Do not delete evidence or reset database statistics to reduce network usage.
