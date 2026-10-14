# Oracle handoff: improvement plan release

Written 14 October 2026. It covers everything on branch `feature/efficiency-pass` that the Oracle
backend (`https://130.61.104.247`) needs.

The changes are all of the following:
- improvement-plan phases 0 to 7;
- the understanding review;
- the fixes from the first live test;
- multi-step execution: saved plans and the coding workspace.

Details for each are in [docs/benchmarks](../benchmarks/).

## 1. Before deploying

- **Commit and push the branch.** It is not committed yet.
- **Database:** switch Oracle to the backup database, as on the laptop (section 3).
- **GitHub App:** it currently points at the laptop tunnel. Point it back at Oracle (section 5)
  once the live test is finished.

## 2. New dependencies

**None.** No Python or frontend packages were added:
- `backend/pyproject.toml` is unchanged;
- `frontend/package.json` and `package-lock.json` are unchanged.

| Item | Where | Needed on Oracle? |
|---|---|---|
| `cloudflared` 2026.10.0 (installed with winget) | Laptop only, for the temporary tunnel | No |
| `git` and `python3` inside the E2B coding template | E2B sandbox | Already required; workspaces used `git apply` before. Workspace checkpoints now also use `git init`, `git commit` and `git reset` inside the sandbox. |

## 3. Database: switch to the backup database, as done locally

**The original project** (`audoryn-staging`, `ep-small-glade`) is suspended until 1 November 2026
(free-plan network-transfer limit).

**On the laptop,** `DATABASE_URL` was switched to the backup project
(`Audoryn-staging-Backup`, `ep-long-glitter`), with the original kept as a comment:

```bash
# Primary database: Neon project audoryn-staging (ep-small-glade). Suspended until 1 November 2026
# (free-plan network transfer limit). To switch back: uncomment the next line and comment out the backup.
# DATABASE_URL=<original value>
# Backup database: Neon project Audoryn-staging-Backup (ep-long-glitter), active since 12 October 2026.
DATABASE_URL=<backup value, from the laptop's backend/.env>
```

**On Oracle:**
- **The same change:** make exactly this change in Oracle's environment.
- **No migrations:** the backup database is already at the latest migration
  (`0017_run_plan_steps`).
- **One backend at a time:** stop the laptop backend and its tunnel before Oracle starts using the
  database.

### Keeping Neon transfer down (why the original was suspended)

- **Where it went:** transfer counts every result sent to the backend. The original project used
  5.56 GB in October for a 47 MB database. The efficiency pass and phases 1 and 7 cut the main
  sources; see [database-transfer.md](../database-transfer.md).
- **Ways to keep it low:**
  - Stop backends you are not using: an idle one still runs sweeps and recovery.
  - `USAGE_RECORDS_ENABLED=true` adds one small row per runtime step and chat message. Set it to
    `false` on the free plan if transfer is tight.
  - Watch the Neon Console's transfer graph for the first days after deploying.
- **Distance:** running the backend close to the database's region makes each step faster (from
  the laptop each query took about 300 ms), but it does not change transfer.

### Migrations in this release

| Migration | Adds |
|---|---|
| `0016_usage_records` | `usage_records` table |
| `0017_run_plan_steps` | `run_plan_steps` table (saved run plans) |

Both are already on the backup database. Run them (`alembic upgrade head`) only on the original
database, if it is used again after 1 November.

## 4. Environment settings

### New settings (all optional; the defaults are the recommended values)

| Setting | Default | What it does |
|---|---|---|
| `USAGE_RECORDS_ENABLED` | `true` | Saves compute per runtime step and chat message (`usage_records`) |
| `PLANNER_STREAM_SILENCE_SECONDS` | `300` | A model that sends nothing for this long counts as failed. A model still producing output is never cut off. |
| `PREPARATION_RECOVERY_IDLE_SECONDS` | `600` | Idle time before task preparation is recovered |
| `QSTASH_MAX_DELAY_SECONDS` | `604800` | Longest QStash delay (7 days on the free plan); schedules further ahead are reached in hops |
| `APPROVAL_REMINDER_HOURS` | `12` | Reminder for a pending approval |
| `APPROVAL_EXPIRY_HOURS` | `24` | A pending approval expires: the action is cancelled and the task needs attention |
| `INTEGRATION_TOKEN_REFRESH_LEAD_SECONDS` | `300` | Integration tokens are renewed this long before they expire |
| `INTEGRATION_RATE_LIMIT_MAX_WAITS` | `12` | Waits for a provider's rate limit (or a free slot) before an action needs attention; waits are not failures |
| `OUTBOX_DRAIN_COALESCE_SECONDS` | `60` | One outbox drain request at a time; this bounds how long a lost request holds others back |
| `RUNTIME_MAX_PLAN_STEPS` | `60` | Most steps in a run's saved plan |
| `RUNTIME_MAX_PLAN_ACTIONS` | `120` | Most actions in a planned run, including retries (2 per plan step, at least 20) |
| `RUNTIME_MAX_PLAN_REVISIONS` | `3` | Times a run may revise its plan, each with a reason |
| `CODING_QUEUE_MAX_WAIT_SECONDS` | `7200` | Longest a task waits in the coding-workspace queue |

`.env.example` lists them all.

### Existing settings changed during testing (copy to Oracle as you decide)

| Setting | Value on the laptop | Note |
|---|---|---|
| `AI_COMPLEX_ROUTES` | `["nvidia_nim:configured","groq:qwen/qwen3.8-27b","nvidia_nim:moonshotai/kimi-k3","nvidia_nim:openai/gpt-oss-20b","nvidia_nim:poolside/laguna-xs-2.1"]` | Planner model order, with Nemotron first (owner decision). Before, Groq qwen was first. |
| `AI_INTERACTIVE_ROUTES` | `["nvidia_nim:configured","nvidia_nim:openai/gpt-oss-20b","groq:qwen/qwen3.8-27b","openrouter:cohere/north-mini-code:free","nvidia_nim:z-ai/glm-5.3","nvidia_nim:poolside/laguna-xs-2.1","nvidia_nim:moonshotai/kimi-k3"]` | Chat model order, with Nemotron first (owner decision). Before, gpt-oss-20b was first. |
| `GITHUB_EVENTS_ENABLED` | `true` | Needed for event triggers; `GITHUB_WEBHOOK_SECRET` must match the GitHub App's webhook secret |
| `DATABASE_URL` | the backup database | See section 3 |

### Laptop-only values (do not copy)

The laptop `.env` points these at the temporary tunnel
(`https://divisions-affairs-scripts-hosts.trycloudflare.com`):
- `QSTASH_*_URL` and `UPSTASH_QSTASH_*_URL`: execute, outbox drain, sweep and failures;
- `GITHUB_CALLBACK_URL`;
- `GITHUB_LOGIN_CALLBACK_URL`.

Oracle keeps its own `https://130.61.104.247/...` values.

**Settings removed:** none. **Defaults changed:** none.

## 5. External services

**GitHub App (Audoryn).**
- **URLs:** set them back to Oracle after the live test.

  | Field | URL |
  |---|---|
  | Callback URL 1 | `https://130.61.104.247/integrations/github/callback` |
  | Callback URL 2 | `https://130.61.104.247/api/v2/auth/github/callback` |
  | Setup URL (post installation) | `https://130.61.104.247/api/v2/auth/github/installation/callback` |
  | Webhook URL | `https://130.61.104.247/integration-events/github` |

- **Webhook:** keep it **Active**, and keep the subscribed events (at least **Issues**). Before the
  live test the app subscribed to no events, so GitHub sent nothing.
- **Note for production:** GitHub must accept the server's HTTPS certificate for webhook
  deliveries. With a bare IP address and a self-signed certificate, either turn off "SSL
  verification" in the webhook settings (not recommended), or use a domain with a valid
  certificate.

**QStash.**
- **Schedules:** no changes. The 10-minute runtime sweep and the 15-minute outbox recovery drain
  still point at Oracle.
- **Sweep:** it now also does coding-session clean-up and the stale web-research check, which used
  to run on every drain.
- **Fewer messages:** commits now send at most one drain request at a time.

**Upstash Redis.** No setup needed. New short-lived keys appear on their own:
- `flag:outbox-drain-pending`;
- `queue:coding:<org>` and `queue-seen:coding:<org>`;
- `cache:integration-cooldown:<connection>`.

Live events are no longer copied into `audoryn:runs:*` and `audoryn:worker:*` streams, which
nothing read. Old ones expire under their length limits.

**E2B.** No template change needed (it already needs `git` and `python3`).

## 6. What changes on first start

- **Old approvals:** pending approvals older than 24 hours expire on the first sweep; the action is
  cancelled and the task needs attention.
- **Runs in progress:** they write a plan at their next planning step and continue under it.
- **Discovery:** the first "Rediscover" of a connection may mark long-gone resources as
  unavailable. That correction is intended.
- **Approvals for visible writes:** writes other people can see (comments, issues, PRs, messages)
  now ask for approval unless the request asked for them. This applies to tasks and workers
  created from now on.
- **Paused job:** the trigger job created in the live test, which commented on every new issue,
  is paused in the development database.

## 7. Deploy steps

1. Stop the laptop backend and its tunnel.
2. Pull the branch on the server.
3. Install backend dependencies as usual; nothing new.
4. Switch `DATABASE_URL` to the backup database (section 3); no migrations to run.
5. Update the environment (section 4), then restart the backend.
6. Rebuild and redeploy the frontend as usual. Changes include:
   - the GitHub setup redirect fix;
   - the approval Inbox;
   - "Test before saving" on policies;
   - plan progress in the task activity.
7. Point the GitHub App back at Oracle (section 5).

## 8. Checks after deploying

- `GET /health/live` returns 200.
- A QStash message to `/internal/v1/runtime/sweep` returns 200 in the QStash log.
- **A chat read:** "What are the open issues in <repo>?" starts a task with a fresh read.
- **A requested comment** runs. A comment the AI chose on its own waits for approval in the Inbox.
- **An event trigger:**
  - "When a new issue is opened in <repo>, summarise it here" replies "Set up: …";
  - opening an issue then posts a summary in the chat;
  - GitHub's webhook delivery log shows 200.
- **A coding task:** the task activity shows "Step N of M". Edits return short diffs, and there is
  one approval before publishing.

## 9. Rolling back

- **Code:** redeploy the previous commit (`13759fb`).
- **Database:** both migrations only add tables, so they can stay with the old code. To remove
  them: `alembic downgrade 0015_github_login`, which drops `run_plan_steps` and `usage_records`.
- **Switching database:** to go back, restore the previous `DATABASE_URL`.
