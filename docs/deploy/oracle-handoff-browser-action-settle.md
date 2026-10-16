# Oracle handoff: observe the browser page once an action's effect has arrived (F48)

Written 16 October 2026, for pull request #132 (`fix/browser-action-settle`, merge `7c62b52`),
the release after PR #131 (`689de9e`).

It follows *Audoryn Oracle Update and Deployment Rules — Illustrated Edition*: inspect first,
change only what this release needs, and verify the outcome.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`,
  image `localhost/audoryn-api:latest`.
- **Not touched:** Vercel (no frontend change), `/etc/audoryn/audoryn.env`, the database, QStash
  schedules, Console.

## 1. What this release does

After every browser action (page open, click, key, hover, scroll, fill, submit, sign-in, upload)
the page is read once it has settled: no page or data request the action started is still
waiting, and the page has been unchanged for 0.4 s. An empty page keeps being waited for. The wait
never exceeds 5 s. Found on Bestworth, where the page right after signing in was empty and cost an
extra planning round. Details: F48 in `docs/testing/simulation-findings.md`.

## 2. Dependencies, environment and database

- **Dependencies:** none. Rebuild the image only.
- **Environment:** nothing to change.
- **Database:** no migration. `alembic heads` stays `0019_usage_prompt_chars`.
- **Frontend:** no change, so no Vercel deploy.

## 3. Steps (each one only after approval)

1. **Inspect:** block A, including free disk space.
2. **Pull:** block B; `HEAD` becomes `7c62b52`.
3. **Disk:** the PR #131 build ran out of space. If less than 4 GB is free, run block C0 first.
4. **Tag, then rebuild:** block C.
5. **Read-only checks with the new image:** block D.
6. **Restart and verify:** block E, with `sleep 20` after the restart.
7. **Live checks:** section 4.

## 4. Live checks with the real model

| # | Where | What to do | Expected |
|---|---|---|---|
| 1 | Bestworth Admin chat or job | Ask it to sign in and read the saved inquiries | Step 2 (sign-in) is followed by the dashboard content, with no extra "post-login page observation is empty" step |
| 2 | Connections → Bestworth | **Test sign-in** | "Signed in: reached …" |
| 3 | Any browser task | Run it | Works as before; each action takes about 0.4 s longer at most when the page does not change |

```bash
podman logs --since 15m audoryn-api 2>&1 | grep -E "Traceback|still busy after an action|Browser request refused"
```

- A few `Browser page still busy after an action` lines are fine (a page that keeps changing was
  read after 5 s). Many on one site means it never settles; report it.
- No `Traceback`.

## 5. Rolling back

- **Code:** run `podman tag localhost/audoryn-api:rollback-689de9e localhost/audoryn-api:latest`,
  then restart (with `sleep 20`).
- **Database:** nothing changed.

## 6. Proof (laptop)

- **Backend tests:** 1,728 pass (CI-style), including 3 new real-Chromium tests for the wait and
  all 143 existing browser tests.
- **Static checks:** ruff, format, pyright, the changed-code guard and the CI verify scripts pass.
- **Simulator, real Chromium (Test sign-in):** a script-drawn dashboard was read empty before and
  shows "INBOX EMPTY" after; a slow script login was wrongly "still on the login page" before and
  signs in after. The GET-login leak protection (F45) still refuses, with 0 requests reaching the
  site.

## 7. Oracle commands

Run each block only after the previous one is checked. The blocks no longer use `exit`, so a
failed command does not close the SSH session.

**A. Read-only inspection**

```bash
cd /opt/audoryn/AgentGate && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-api.service
df -h / /var/tmp
podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}'
```

- **Expected:** branch `main`, a clean tree, `Current` starting `689de9e`, `Remote` starting
  `7c62b52`.

**B. Fast-forward**

```bash
cd /opt/audoryn/AgentGate && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend/Containerfile backend/pyproject.toml backend/migrations frontend; fi
```

- **Expected:** `After` starts `7c62b52`, and **no files listed**.

**C0. Free space (only if under 4 GB free)**

Removes untagged leftovers only; never `latest` or a rollback tag:

```bash
podman image prune -f && df -h / /var/tmp
```

**C. Tag the rollback image, then rebuild**

```bash
cd /opt/audoryn/AgentGate && podman tag localhost/audoryn-api:latest localhost/audoryn-api:rollback-689de9e && podman build -f backend/Containerfile -t localhost/audoryn-api:latest . && podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

**D. Read-only checks with the new image**

```bash
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python -c "from app.execution.browser.runtime import ACTION_QUIET_SECONDS as q, ACTION_SETTLE_MAX_SECONDS as m, BrowserRuntime as B; print('quiet', q, 'max', m, 'settle', hasattr(B, '_settle_page'))"
```

- **Expected:** `0019_usage_prompt_chars (head)`, then `quiet 0.4 max 5.0 settle True`.

**E. Restart and verify**

```bash
systemctl --user restart audoryn-api.service
sleep 20
systemctl --user is-active audoryn-api.service
curl -sS --max-time 20 http://127.0.0.1:8000/health/ready; echo
curl -sS --max-time 20 https://130.61.104.247/health/ready; echo
[ "$(podman image inspect localhost/audoryn-api:latest --format '{{.Id}}')" = "$(podman inspect --type container audoryn-api --format '{{.Image}}')" ] && echo 'PASS: running the new image' || echo 'FAIL: different image running'
```

**F. Watch the first minutes**

```bash
podman logs --since 5m audoryn-api 2>&1 | grep -E "Traceback|ERROR|startup complete"
```
