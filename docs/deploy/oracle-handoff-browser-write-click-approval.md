# Oracle handoff: hold changes a click sends; replan refused actions (F49, F50)

Written 16 October 2026, for pull request #133 (`fix/browser-write-click-approval`, merge
`fdf4402`), the release after PR #132 (`7c62b52`).

It follows *Audoryn Oracle Update and Deployment Rules — Illustrated Edition*: inspect first,
change only what this release needs, and verify the outcome.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`,
  image `localhost/audoryn-api:latest`.
- **Vercel:** the frontend (the run page shows "Held, nothing sent").
- **Not touched:** `/etc/audoryn/audoryn.env`, the database, QStash schedules, Console.

## 1. What this release does

- **F50:** while a click, check or uncheck runs, a change it would send to the site is held before
  it leaves the browser. The step shows "Held, nothing sent", and the worker sends the change with
  `browser.form.submit` on the same button, where policy can ask for approval. **Always allow** on
  that approval lets this worker do it on this site without asking again. Reads, links, beacons,
  sign-ins, uploads and typing are unchanged.
- **F49:** an action refused before anything was sent is replanned ("Nothing was dispatched"), in
  every task, instead of showing "Uncertain outcome" or failing a chat browser task.

**Behaviour change to expect:** a worker that must make such a change needs `browser.form.submit`
among its job's capabilities, and the first such change on a site may ask for approval.

## 2. Dependencies, environment and database

- **Dependencies:** none. Rebuild the image only.
- **Environment:** nothing to change.
- **Database:** no migration. `alembic heads` stays `0019_usage_prompt_chars`.

## 3. Steps (each one only after approval)

1. **Inspect:** block A, including free disk space and the images.
2. **Pull:** block B; `HEAD` becomes `fdf4402`.
3. **Disk:** if less than 4 GB is free, run block C0 (removes the oldest rollback image only).
4. **Tag, then rebuild:** block C.
5. **Read-only checks with the new image:** block D.
6. **Restart and verify:** block E, with `sleep 20` after the restart.
7. **Vercel:** deploy the frontend at `fdf4402` (separate approval; auto-deploy is off).
8. **Live checks:** section 4.

## 4. Live checks with the real model

| # | Where | What to do | Expected |
|---|---|---|---|
| 1 | Bestworth chat | Ask it to disable the Test worker in Worker Access | The click step shows **Held, nothing sent**; then an approval for the governed submit; after approving, the worker shows Disabled |
| 2 | Same request again (after re-enabling Test), having used **Always allow** in check 1 | Run it | No approval is asked; the worker is disabled |
| 3 | Any read-only browser task (Bestworth inquiries) | Run it | Works as before; no held changes |

```bash
podman logs --since 15m audoryn-api 2>&1 | grep -E "Traceback|held for approval|Runtime interrupted|Runtime stopped"
```

- `Browser change held for approval … method=POST url=…` is F50 working.
- No `Traceback`, and no `Runtime interrupted` for a refused action.

## 5. Rolling back

- **Code:** run `podman tag localhost/audoryn-api:rollback-7c62b52 localhost/audoryn-api:latest`,
  then restart (with `sleep 20`).
- **Database:** nothing changed.
- **Frontend:** redeploy `7c62b52` on Vercel.

## 6. Proof (laptop)

- **Backend tests:** 1,738 pass (CI-style); 10 new tests, the browser ones in real Chromium against
  a real local web server.
- **Frontend:** 158 tests pass; typecheck and build pass.
- **Static checks:** ruff, format, pyright, the changed-code guard and the CI verify scripts pass.
- **Simulator end to end:** a refused auto-accept was replanned; a plain click's change was held
  and nothing reached the site; the governed submit asked for approval and, approved with Always
  allow, made the change; the same request again ran without approval. Details: F49 and F50 in
  `docs/testing/simulation-findings.md`.

## 7. Oracle commands

Run each block only after the previous one is checked. The blocks do not use `exit`, so a failed
command does not close the SSH session.

**A. Read-only inspection**

```bash
cd /opt/audoryn/AgentGate && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-api.service
df -h / /var/tmp
podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}'
```

- **Expected:** branch `main`, a clean tree, `Current` starting `7c62b52`, `Remote` starting
  `fdf4402`.

**B. Fast-forward**

```bash
cd /opt/audoryn/AgentGate && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend/Containerfile backend/pyproject.toml backend/migrations; fi
```

- **Expected:** `After` starts `fdf4402`, and **no files listed**.

**C0. Free space (only if under 4 GB free)**

Keeps `latest` and `rollback-689de9e`; removes the older rollback and untagged leftovers:

```bash
podman rmi localhost/audoryn-api:rollback-d3257e0; podman image prune -f && df -h / /var/tmp
```

**C. Tag the rollback image, then rebuild**

```bash
cd /opt/audoryn/AgentGate && podman tag localhost/audoryn-api:latest localhost/audoryn-api:rollback-7c62b52 && podman build -f backend/Containerfile -t localhost/audoryn-api:latest . && podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

**D. Read-only checks with the new image**

```bash
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python -c "from app.execution.browser.runtime import WRITE_GUARDED_OPERATIONS as w; from app.execution.providers.browser import _held_change_summary as h; print(sorted(w)); print(h({'heldRequest': {'method': 'POST', 'url': 'https://x.test/a?b=c'}})[:60])"
```

- **Expected:** `0019_usage_prompt_chars (head)`, then
  `['element.check', 'element.click', 'element.uncheck']` and
  `Nothing was sent: this control sends a change to the site (P`.

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

**G. Vercel (separate approval; auto-deploy is off)**
- Deploy project `audoryn` at exactly `fdf4402` (`main`).
- Wait for `READY`.
- Check that `https://audoryn.vercel.app` serves that SHA.
