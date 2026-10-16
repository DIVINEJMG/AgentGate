# Oracle handoff: the context and quality plan (phases 0–5)

Written 15 October 2026, for pull request #129 (`feature/ai-simulator`, merge `0b5d503`), the
release after PR #128 (`aaa52ab`). It follows *Audoryn Oracle Update and Deployment Rules —
Illustrated Edition*: inspect first, change only what this release needs, and verify the outcome.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`, image
  `localhost/audoryn-api:latest`.
- **Vercel:** the frontend (new buttons in the chat and the Work queue).
- **Not touched on Oracle:** `/etc/audoryn/audoryn.env`, the database (already done from the
  laptop, section 3), QStash schedules, Console.

## 1. What this release does

Everything found by the live AI simulation (`docs/testing/simulation-findings.md`), fixed in six
phases (`docs/plans/context-quality-fix-plan.md`):

- **What the model sees:** compact evidence and step summaries, a planning contract the model can
  follow without outside knowledge, smaller prompts (interpretation 85k → 25k characters,
  preparation 75k → 27k, planning 8.5k–13.6k), corrections that name the wrong value, the JSON
  schema sent once.
- **Chat:** a message is saved before the model is called; a failed reply has a **Retry** button;
  a declined request is closed instead of created.
- **Recovery and messages:** plain wording for approval waits, refused writes, paused planning
  and unconfirmed writes. Chat buttons: **Retry planning**, **It happened / It didn't happen /
  Check again**. **Retry planning** really restarts planning.
- **Approvals:** every write other people can see asks first, including writes the person asked
  for in so many words. The chat's approval card gains **Always allow**.
- **Efficiency:** a step that is due now runs on the server itself instead of through a QStash
  wake-up (QStash keeps delayed wake-ups and recovery): 0 QStash messages per step in the replays
  (a coding task used 28). About 62–68 database statements per step (from 95–120).
- **Robustness found in Phase 5:** a planning slot left by a stopped process frees itself within
  90 seconds (it used to block planning for about 33 minutes after a restart mid-call); a task
  waiting for another task's planning turn no longer uses up its retries; GitHub requests wait up
  to 15 seconds for the installation's turn instead of failing.

## 2. Dependencies and environment

- **Dependencies:** none added or upgraded. Rebuild the image only.
- **Environment: nothing to change in `/etc/audoryn/audoryn.env`.**
  - `RUNTIME_RUN_LOCAL` (new, default `true`): due steps run on the server. Leave it unset. Set
    it to `false` only to go back to a QStash wake-up for every step.
  - `AUDORYN_TRACE_STATEMENTS` (new, optional): a development aid. **Leave it unset on Oracle.**
  - AI text connections may carry `"simulator": true` only for the local simulator; the backend
    refuses to start in production with one. Oracle's connections don't have it.

## 3. Database (already done; nothing to run on Oracle)

Both steps were done from the laptop on 15 October against the database Oracle uses (Neon
project `Audoryn-staging-Backup`, branch `production`, endpoint `ep-long-glitter`):

1. **Migration `0019_usage_prompt_chars`:** adds `usage_records.prompt_chars` (JSONB, default
   `{}`). Undo: `alembic downgrade 0018_organization_onboarding`.
2. **Data:** at the owner's request, `production` now holds an exact copy of the simulation
   branch (`sim`): every row of all 59 tables, 6,342 rows (it had 3,851; nothing on `production`
   was newer than `sim`, so nothing was lost). Expect the simulation's tasks, chats, jobs and one
   extra test organisation. **Rollback:** Neon snapshot `production-before-sim-copy-2026-10-15`
   (`snap-billowing-bar-b9s5kn24`).

**Check on Oracle:** `podman exec audoryn-api alembic current` shows
`0019_usage_prompt_chars (head)` (section 8, block D).

## 4. Steps (each one only after approval)

1. **Pull:** follow rules sections 3 and 12, checkpoints A and B (`git pull --ff-only origin
   main`). `HEAD` should become `0b5d503`. The diff is large (140 files): backend, frontend,
   tests, docs, CI and the simulator scripts.
2. **Disk check:** `df -h /`. If less than 4 GB is free, run `podman image prune -f` first.
3. **Tag, then rebuild.**
4. **Read-only checks with the new image:** the migration state and the new settings.
5. **Start and verify:** only if you want Oracle running. It was stopped at inspection; `sleep 20`
   after the start, then both `/health/ready` endpoints and the running image ID.
6. **Deploy the frontend** on Vercel (approval required; auto-deploy is off).

The exact commands are in section 8.

## 5. Checks after deploying

**The live check with the real model** (the last item of the plan). Send each message in a
worker chat:

| # | Message | Expected |
|---|---|---|
| 1 | `What are the open issues in DIVINEJMG/school` | A fresh read, then the list with numbers, titles and links; no approval needed |
| 3 | `In DIVINEJMG/school, add a README section about setup and a small helper script, then open a draft PR.` | Workspace edits, then an approval card for the branch, then one for the draft pull request; after approving, the draft PR link |
| 7 | `When a new issue is opened in DIVINEJMG/school, summarise it here.` | An event trigger. If it asks whether to add this next to an existing job, answer `keep that one` or say what you want. Then open an issue in the repository: a summary should appear in the chat |

**Watch the log while the checks run:**

```bash
podman logs --since 15m audoryn-api 2>&1 | grep -E "Planner proposal rejected|Planner model accepted|Runtime planning failure|Traceback"
```

```bash
podman logs --since 15m audoryn-api 2>&1 | grep '"kind":"runtime.step"' | grep -oE '"dbStatements":[0-9]+|"qstashMessages":[0-9]+|"outcome":"[a-z_]+"' | paste - - - | tail -20
```

**Expected:**
- `qstashMessages` 0 on steps that continue straight away; `dbStatements` mostly 40–90.
- No `Traceback`.
- `Runtime planning failure` only if the model provider itself fails.

**Real proof:** the answers in the chat and, for scenario 3, the draft pull request on GitHub,
not a `202` from the server.

## 6. Rolling back

- **Code:** run `podman tag localhost/audoryn-api:rollback-aaa52ab localhost/audoryn-api:latest`,
  then restart. The previous code works with the new column (it has a default), so the database
  can stay as it is.
- **Data:** restore the Neon snapshot `production-before-sim-copy-2026-10-15` from the Neon
  console (owner's decision; it removes the simulation's data again).
- **Only the in-process steps:** set `RUNTIME_RUN_LOCAL=false` in the environment and restart.
  That is an env edit under rules section 9: a backed-up `sudo python3` script.

## 7. Proof

- **Replays on the simulation stack:**
  - scenario 1 (open issues) completed;
  - scenario 3 (coding task to draft PR #8) completed after its approvals;
  - a backend restart mid-task was recovered by the watchdog.
- **Tests:** 1,664 backend tests pass, also with no `.env` (as CI runs them), plus the security,
  parity and contract suites.
- **Static checks:**
  - ruff, the format checks, pyright and the changed-code quality guard are clean;
  - every CI verify and audit script passes;
  - the frontend typecheck and build pass.
- **Numbers:** `docs/testing/baseline-metrics.md`, "Phase 1–5 results".
- **Targets close but not met** (accepted, with reasons):
  - interpretation and preparation prompts (target 15k), see finding F4;
  - statements per step (target 60): the post-planning permission re-check is kept on purpose.

## 8. Oracle commands

These follow the rules' checkpoints (sections 3, 12, 15, 16 and 17). Run each block only after
the previous one is checked, and stop at any `STOP` or `FAIL`.

**Found at inspection (15 October):**
- A clean tree at `aaa52ab`.
- `audoryn-api.service` **inactive**, with no container running.
- 7.1 GB free.

The service is stopped, so **starting it is a separate decision** (rules section 6). Block E
starts it only once that is approved.

**Copied data, checked read-only before starting:**
- Nothing is queued, admitted or running, so the sweep resumes no simulation task.
- The three tasks shown as waiting for approval (`ac73fcb3`, `53fe95b9` and the PR #8 merge
  `fed7ea24`) all had their approvals **rejected**, so they will not run.
- Four tasks are `waiting_ai` and two `uncertain_outcome`; they move only when someone presses
  their buttons.
- No pending approvals and no unpublished outbox events.

**A. Checkpoint A: read-only inspection**

```bash
cd /opt/audoryn/AgentGate || exit 1
pwd
git branch --show-current
git status --short
git fetch origin main || exit 1
printf 'Current: '; git rev-parse HEAD
printf 'Remote:  '; git rev-parse origin/main
git -c core.pager=cat diff --stat HEAD origin/main -- backend/ frontend/ | tail -3
```

- **Expected:** branch `main`, a clean tree, `Current` starting `aaa52ab`, `Remote` starting
  `0b5d503`.

**B. Checkpoint B: the approved fast-forward**

```bash
cd /opt/audoryn/AgentGate || exit 1
if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then
  echo 'STOP: wrong branch or dirty working tree'
else
  OLD="$(git rev-parse HEAD)"
  git pull --ff-only origin main && NEW="$(git rev-parse HEAD)"
  if [ -n "${NEW:-}" ]; then
    printf 'Before: %s\nAfter:  %s\n' "$OLD" "$NEW"
    git -c core.pager=cat diff --name-only "$OLD" "$NEW" -- backend/Containerfile backend/pyproject.toml backend/migrations
  else
    echo 'STOP: pull failed'
  fi
fi
```

- **Expected:** `After` starts `0b5d503`.
- **Files listed:** only `backend/pyproject.toml` (a type-checking setting, no dependency) and
  `backend/migrations/versions/0019_usage_prompt_chars.py`.

**C. Tag the rollback image, then rebuild (approved)**

```bash
cd /opt/audoryn/AgentGate || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
podman tag localhost/audoryn-api:latest localhost/audoryn-api:rollback-aaa52ab || exit 1
podman build -f backend/Containerfile -t localhost/audoryn-api:latest . || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

**D. Read-only checks with the new image (no start needed)**

```bash
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest alembic heads
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python - <<'PY'
from app.bootstrap.settings import settings
print("environment:", settings.environment)
print("runtime enabled:", settings.runtime_execution_enabled)
print("due steps run on the server (RUNTIME_RUN_LOCAL):", settings.runtime_run_local)
print("simulator connection configured:", any(c.simulator for c in settings.ai_text_connections.values()))
print("watchdog seconds:", settings.delivery_watchdog_seconds)
PY
```

- **Expected:** `heads` and `current` both `0019_usage_prompt_chars (head)`, so no migration
  is run.
- **Expected settings:** `runtime enabled: True`, `due steps run on the server: True`,
  `simulator connection configured: False`.

**E. Start and verify (only if you want Oracle running)**

```bash
systemctl --user start audoryn-api.service
sleep 20
systemctl --user is-active audoryn-api.service
podman ps --filter name=audoryn-api --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
curl -sS -i --max-time 20 http://127.0.0.1:8000/health/ready
curl -sS -i --max-time 20 https://130.61.104.247/health/ready
IMAGE_ID="$(podman image inspect localhost/audoryn-api:latest --format '{{.Id}}')"
RUNNING_ID="$(podman inspect --type container audoryn-api --format '{{.Image}}')"
[ "$IMAGE_ID" = "$RUNNING_ID" ] && echo 'PASS: running the new image' || echo 'FAIL: different image running'
```

**F. Watch the first minutes**

```bash
podman logs --since 5m audoryn-api 2>&1 | grep -E "Traceback|ERROR|startup complete"
```

**G. Vercel (separate approval; auto-deploy is off)**
- Deploy project `audoryn` at exactly `0b5d50399…` (`main`).
- Wait for `READY`.
- Check that `https://audoryn.vercel.app` serves that SHA.

**Before the live check:**
- **QStash schedules** (heartbeat, sweep, outbox drain) must target `https://130.61.104.247/...`,
  not a laptop tunnel.
- **The GitHub App webhook** must be `https://130.61.104.247/integration-events/github` for
  scenario 7.
- Both were pointed elsewhere during local work at times; verify them, don't assume.
