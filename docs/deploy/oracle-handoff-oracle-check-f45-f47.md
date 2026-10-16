# Oracle handoff: Oracle check F45–F47 (script logins, browser discovery, merge confirmation)

Written 16 October 2026, for pull request #131 (`fix/oracle-check-f45-f47`, merge `689de9e`),
the release after PR #130 (`d3257e0`).

It follows *Audoryn Oracle Update and Deployment Rules — Illustrated Edition*: inspect first,
change only what this release needs, and verify the outcome.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`,
  image `localhost/audoryn-api:latest`.
- **Vercel:** the frontend.
- **Not touched:** `/etc/audoryn/audoryn.env`, the database, QStash schedules, Console.

## 1. What this release does

The three problems the Oracle live check of PR #130 found. Details are in
`docs/testing/simulation-findings.md` (F45–F47).

- **F45: Bestworth Test sign-in.** Its login form has no method (the site's script sends it),
  and was refused as a GET form. The form's method is no longer checked. Instead, any request
  whose web address carries a typed secret (password, one-time code, fields named like
  pass/otp/secret/token/pin) is refused, plain or encoded. Usernames and emails are not secrets.
- **F46: false "Discovering resources" failure.** Connecting a browser skips that step, and a
  browser connection's page no longer offers **Rediscover**.
- **F47: merge never confirmed.** GitHub's answer lacked `merge_commit_sha`. A merge is now
  confirmed when GitHub says the pull request is merged at the approved head; the other fields
  are optional. Any unreadable answer after a write is "uncertain", not a crash. While the
  outcome is checked again, the chat says so once.

## 2. Dependencies, environment and database

- **Dependencies:** none. Rebuild the image only.
- **Environment:** nothing to change in `/etc/audoryn/audoryn.env`.
- **Database:** no migration. `alembic heads` stays `0019_usage_prompt_chars`.

## 3. Steps (each one only after approval)

1. **Inspect:** block A. Note whether `audoryn-api.service` is running.
2. **Pull:** block B; `HEAD` becomes `689de9e`.
3. **Disk:** `df -h /`. If less than 4 GB is free, run `podman image prune -f` first.
4. **Tag, then rebuild:** block C.
5. **Read-only checks with the new image:** block D.
6. **Restart and verify:** block E, with `sleep 20` after the restart.
7. **Vercel:** deploy the frontend at `689de9e` (separate approval; auto-deploy is off).
8. **Live checks:** section 4.

## 4. Live checks with the real model

| # | Where | What to do | Expected |
|---|---|---|---|
| 1 | Connections → the Bestworth browser connection | **Live checks**, then **Test sign-in** | "Opened the start page: …", then "Signed in: reached …" (not the GET-form refusal) |
| 2 | Connections → connect a new browser connection | Finish the connect flow | "Discovering resources" shows as skipped, not failed; no **Rediscover** on its page |
| 3 | A merge task (PR in DIVINEJMG/school) | Approve the merge | "Merged …". If GitHub's answer is lost: one "checking the outcome" message, then "Merged …" or **It happened / It didn't happen** |
| 4 | A browser job behind the Bestworth login | Run it | It signs in and reads past the login page |

**Watch the log while checking:**

```bash
podman logs --since 15m audoryn-api 2>&1 | grep -E "Traceback|login secret was in its web address|GitHub action could not be read|Browser authentication"
```

- `a login secret was in its web address` means a site really tried to put the password in a
  web address, and it was stopped. It should **not** appear for Bestworth.
- No `Traceback`.

## 5. Rolling back

- **Code:** run `podman tag localhost/audoryn-api:rollback-d3257e0 localhost/audoryn-api:latest`,
  then restart (with `sleep 20`).
- **Database:** nothing changed.
- **Frontend:** redeploy `d3257e0` on Vercel.

## 6. Proof (laptop, simulation stack)

- **Backend tests:** 1,724 pass. One timing-sensitive test
  (`test_correction_consumes_same_attempt_allowance`) failed under full-suite load and passes
  alone (18 of 18); this release does not touch it.
- **Frontend:** 158 tests pass; typecheck and build pass.
- **Static checks:** ruff, the format checks, pyright, the changed-code quality guard and the
  CI verify scripts are clean.
- **Simulator, real Chromium (Test sign-in):** a normal login and a script login with no form
  method both signed in; a form that would send the password in the web address was refused,
  and no request reached the site.

## 7. Oracle commands

Run each block only after the previous one is checked, and stop at any `STOP` or `FAIL`.

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
systemctl --user is-active audoryn-api.service
df -h /
```

- **Expected:** branch `main`, a clean tree, `Current` starting `d3257e0`, `Remote` starting
  `689de9e`.

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

- **Expected:** `After` starts `689de9e`, and **no files listed** (no dependency, image or
  migration change).

**C. Tag the rollback image, then rebuild (approved)**

```bash
cd /opt/audoryn/AgentGate || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
podman tag localhost/audoryn-api:latest localhost/audoryn-api:rollback-d3257e0 || exit 1
podman build -f backend/Containerfile -t localhost/audoryn-api:latest . || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

**D. Read-only checks with the new image**

```bash
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python - <<'PY'
from app.execution.browser.credentials import BrowserCredentialBundle as B
from app.execution.browser.runtime import _carries_secret
print("secret fields (expect True True False False):",
      B.is_secret("password"), B.is_secret("otpSecret"), B.is_secret("username"), B.is_secret("email"))
print("secret in address (expect True False):",
      _carries_secret("https://x.test/login?p=hunter%212", {"hunter!2"}),
      _carries_secret("https://x.test/home", {"hunter!2"}))
PY
```

- **Expected:** `0019_usage_prompt_chars (head)`, then the two expected lines.

**E. Restart and verify**

If the service was inactive at inspection, starting it is a separate decision (rules section 6).

```bash
systemctl --user restart audoryn-api.service
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
- Deploy project `audoryn` at exactly `689de9e` (`main`).
- Wait for `READY`.
- Check that `https://audoryn.vercel.app` serves that SHA.
