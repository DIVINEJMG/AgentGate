# Oracle handoff: Oracle live-check findings F28–F44 (browser end to end, chat browser tasks)

Written 16 October 2026, for the branch `fix/rejected-approval-finishes` (commit `f0452dd`),
the release after PR #129 (`0b5d503`). Fill in the merge commit once the pull request is
merged; the commands below call it `<MERGE>`.

It follows *Audoryn Oracle Update and Deployment Rules — Illustrated Edition*: inspect first,
change only what this release needs, and verify the outcome.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`,
  image `localhost/audoryn-api:latest`.
- **Vercel:** the frontend.
- **Not touched:** `/etc/audoryn/audoryn.env`, the database, QStash schedules, Console.

## 1. What this release does

Everything found by the Oracle live check of 15 October, plus what proving the fixes found.
Details are in `docs/testing/simulation-findings.md`; the plan is in
`docs/plans/oracle-findings-fix-plan.md`.

- **Outcomes:**
  - F28: a rejected approval ends its task.
  - F31: a merge GitHub carried out but whose answer could not be read is "uncertain", not
    "failed".
  - F33: a cut-off answer is asked of the same model again before any fallback.
  - F32: empty reads say what was empty.
- **Activity page:** no longer crashes on runtime actions (F30).
- **Browser connections (Phase B):**
  - Live checks really open the start page; a **Test sign-in** button tries the stored login
    (F34).
  - Login field names reach planning (F35); one-time codes from a stored `otpSecret` (F36).
  - Drafting knows which sites have a login (F37).
  - After a form submit, the next page is read once it has loaded, even on slow sites (F38).
  - A page's own requests follow the blocked-sites and private-network rules; the RAM rule for
    images is unchanged (F39).
  - Downloaded files are listed on the result (F41).
- **Chat browser tasks (Phase C):**
  - A website task from chat runs as a one-time job of the worker, and never asks for an
    account of a tool the person did not name (F29).
  - Files attached to the request can be uploaded (F40).
  - Downloaded files appear on the chat result with an open button.
  - Chat tasks and AI-created jobs use the person's own browser connection for the sites they
    name (F43). A connection with private network on is used only for a site the person named.
  - Browser steps are described by what they did (F44).

**Behaviour change to expect (F43):** a browser connection set up in Connections is now used by
chat tasks and AI-created jobs for the sites the message names, with its login, rules and file
setting. Before, these tasks always made their own bare "Managed web · …" connection.

## 2. Dependencies, environment and database

- **Dependencies:** none added or upgraded. WebSocket routing (F39) needs Playwright 1.48 or
  later; the existing pin `playwright>=1.55,<2` already covers it. Rebuild the image only.
- **Environment:** nothing to change in `/etc/audoryn/audoryn.env`.
- **Database:** no migration. `alembic heads` stays `0019_usage_prompt_chars`.
- **Data note:** `production` holds a copy of the simulation data (15 October). It includes
  browser connections for the simulator's test site (`http://127.0.0.1:8788`, `localhost:8788`)
  and a "Managed web · 127.0.0.1" connection. On Oracle these point at nothing; they can be
  disconnected in Connections and are harmless otherwise.
- **Object storage (F42, decision 16 October):** the simulation's test files in the production
  bucket stay until release cleanup.

## 3. Steps (each one only after approval)

1. **Inspect:** block A. Note whether `audoryn-api.service` is running.
2. **Pull:** block B (`git pull --ff-only origin main`); `HEAD` becomes `<MERGE>`.
3. **Disk:** `df -h /`. If less than 4 GB is free, run `podman image prune -f` first.
4. **Tag, then rebuild:** block C.
5. **Read-only checks with the new image:** block D.
6. **Restart and verify:** block E, with `sleep 20` after the restart.
7. **Vercel:** deploy the frontend at `<MERGE>` (separate approval; auto-deploy is off).
8. **Live checks:** section 4.

## 4. Live checks with the real model

Before starting, make sure the QStash schedules and the GitHub App webhook point at
`https://130.61.104.247`, not at a laptop tunnel.

| # | Where | What to do | Expected |
|---|---|---|---|
| 1 | Connections → a browser connection with a login | **Live checks**, then **Test sign-in** | "Opened the start page: …", then "Signed in: reached …" (or a plain reason) |
| 2 | Bestworth Admin chat | `Using the browser integration Navigate to the bestworthproductsltd.ng. Read out all products in the product section` | "I started a one-time task … on bestworthproductsltd.ng", **no GitHub question**, then the products in the chat |
| 3 | A chat, with a file attached | Ask the worker to upload the attached file on a site whose connection has file upload on | Approval cards for the upload and the submit; the site's reply in the result |
| 4 | A chat | Ask for a file to be downloaded from a site with file actions on | The chat result shows the file; its button opens it |
| 5 | A browser job behind a login (the Bestworth dashboard) | Run it | It signs in with the stored login and reads past the login page |
| 6 | Activity page | Open it | No crash; actions show their policy |
| 7 | A merge task (PR in DIVINEJMG/school) | Approve the merge | "Merged …", or "uncertain" with **It happened / It didn't happen** if GitHub's answer was lost |

**Also watch for:** a new chat message refused with "local AI request capacity is busy" while
another task is planning (seen once in the simulator).

**Watch the log while checking:**

```bash
podman logs --since 15m audoryn-api 2>&1 | grep -E "Traceback|Runtime planning failure|Browser request refused|GitHub action could not be read"
```

- `Browser request refused …` lines are F39 working: a page asked for a blocked site or a
  private address.
- No `Traceback`.

## 5. Rolling back

- **Code:** run `podman tag localhost/audoryn-api:rollback-0b5d503 localhost/audoryn-api:latest`,
  then restart (with `sleep 20`).
- **Database:** nothing changed, so there is nothing to roll back.
- **Frontend:** redeploy `0b5d503` on Vercel.

## 6. Proof (laptop, simulation stack)

- **Backend tests:** 1,722 pass (CI-style, no `.env`).
- **Frontend:** 158 tests pass; typecheck and build pass.
- **Static checks:** ruff, the format checks, pyright and the changed-code quality guard are
  clean.
- **Simulator, real Chromium:**
  - live check and Test sign-in (right and wrong password);
  - one-time-code sign-in (right, wrong and no secret);
  - a slow (3 s) login;
  - site rules and private network (navigations, redirects, direct reads, page scripts and
    WebSockets);
  - file download and upload.
- **Scenario s8 (chat browser task):** a chat request became a one-time job on the person's
  connection. It downloaded report.csv, uploaded the attached file ("Received stock-update.csv
  (237 bytes)") with three approvals, finished, and showed report.csv on the chat result.
- **Not re-run before commit:** simulator scenarios s1 and s3 (owner's decision, usage). CI
  covers the same code with tests.

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

- **Expected:** branch `main`, a clean tree, `Current` starting `0b5d503`, `Remote` = `<MERGE>`.

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

- **Expected:** `After` = `<MERGE>`, and **no files listed** (no dependency, image or migration
  change).

**C. Tag the rollback image, then rebuild (approved)**

```bash
cd /opt/audoryn/AgentGate || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
podman tag localhost/audoryn-api:latest localhost/audoryn-api:rollback-0b5d503 || exit 1
podman build -f backend/Containerfile -t localhost/audoryn-api:latest . || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

**D. Read-only checks with the new image**

```bash
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest alembic heads
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python - <<'PY'
import importlib.metadata as m
from playwright.async_api import BrowserContext
from app.execution.browser.credentials import one_time_code
print("playwright:", m.version("playwright"), "| websocket routing:", hasattr(BrowserContext, "route_web_socket"))
print("one-time code (RFC 6238 vector, expect 94287082):",
      one_time_code("otpauth://totp/x?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&digits=8", at=59))
PY
```

- **Expected:** `heads` and `current` both `0019_usage_prompt_chars (head)`;
  `websocket routing: True`; the code `94287082`.

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
- Deploy project `audoryn` at exactly `<MERGE>` (`main`).
- Wait for `READY`.
- Check that `https://audoryn.vercel.app` serves that SHA.
