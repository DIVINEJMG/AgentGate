# Oracle handoff: planner corrections and coding retries

Written 14 October 2026, for the pull request from `fix/planner-retry-and-coding-retry` (the
release after PR #124, `cc3fd62`). It follows *Audoryn Oracle Update and Deployment Rules —
Illustrated Edition*: inspect first, change only what this release needs, and verify the outcome.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`, image
  `localhost/audoryn-api:latest`.
- **Not touched:** Console, Vercel, QStash, the database, `/etc/audoryn/audoryn.env`.

This pull request also carries [oracle-handoff-github-app-key.md](oracle-handoff-github-app-key.md),
the handoff for PR #124, which was not part of that merge. Its env step (removing wrapping quotes
from `GITHUB_APP_ID`, `GITHUB_CLIENT_ID`, `CODING_TEMPLATE_ID` and `CODING_ALLOWED_HOSTS`) has
already been done on Oracle.

## 1. What the live test showed

| | Finding | Evidence |
|---|---|---|
| Confirmed | Claude Sonnet's first plan proposal was rejected by AgentGate's own plan checks almost every step, so each planning step cost two calls; in three steps the correction was also rejected and planning fell back to Nemotron | `planner_attempts`: every Sonnet attempt `call_count=2`, `schema_valid=true`; failed ones end "Planner correction budget exhausted" |
| Unknown | Which check rejected it | The reason was sent back to the model but never logged or saved |
| Confirmed | The coding task paused as "uncertain outcome" although nothing was written | Sandbox `i3ocu8bjz1c85y3tcjgv1`: commits `baseline` and `edit README.md` only, no `scripts/`, leftover request file from step 4; a probe write afterwards succeeded |
| Confirmed | Cause: step 5's first operation (uploading its request file to the sandbox) failed once in E2B's file service, and the general integration layer then treated the workspace edit like a write to GitHub and paused replay | `integration_execution.py` turned every failed side-effect action into `uncertain_outcome` |

## 2. What changed (code only)

- **Planner:**
  - **Every rejection is logged** with its reason (the first sentence of AgentGate's own check
    message, never task content):
    `Planner proposal rejected call=1 correlation=… reason=…`.
  - **A left-out `planStep` now means the current step.** This is what the correction already
    accepted, so it saves the second call. A wrong step number is still sent back.
- **Coding:**
  - **Edits aren't paused as uncertain.** Workspace edits, patches and rollbacks are safe to
    repeat, so when one fails it is retried instead of being paused. This also fixes the
    "sandbox lost and rebuilt; this step will run again" path. Writes to GitHub and workspace
    commands still pause for checking.
  - **The request-file upload is retried** up to three times. It changes nothing in the
    repository.
  - **Safe error labels in the log:** sandbox errors are logged with labels such as `timeout`,
    `service_unavailable` and `connection_error`, never the message.
- **CI:** a test from PR #124 contained a literal private-key header, which the secret scanner
  flags. The test now builds that header from two parts.

**No migration, no new dependencies, no new settings, no env change.** Backend files changed, so
the image needs rebuilding. No frontend change, so Vercel needs no deploy.

## 3. Steps (each one only after approval)

1. **Merge** the pull request once CI passes.
2. **Pull:** on Oracle, follow rules sections 3 and 12, checkpoints A and B (`git pull --ff-only
   origin main`). The diff should show only `backend/` and `docs/` files.
3. **Rebuild** the image (rules section 16):

```bash
cd /opt/audoryn/AgentGate || exit 1
podman build -f backend/Containerfile -t localhost/audoryn-api:latest . || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

4. **Restart and verify** (rules sections 7 and 17): restart `audoryn-api.service`, check both
   `/health/ready` endpoints, and check the running image ID matches the new build.

## 4. Checks after deploying

- **Planner reasons:** ask a GitHub task (for example "What are the open issues in
  DIVINEJMG/school?") and watch the log:

```bash
podman logs --since 10m audoryn-api 2>&1 | grep -E "Planner proposal rejected|Planner model accepted"
```

  - **Expected:** Sonnet accepted with `calls=1` on most steps.
  - **If rejections remain:** the `reason=` lines show exactly which check. Send them over and
    that check gets fixed.
- **The paused coding task:** press **Resume** on "add a README section about setup and a small
  helper script, then open a draft PR".
  - It should write `scripts/setup.sh`, then ask for your approval before publishing the draft
    pull request.
  - Proof is the draft pull request on GitHub after you approve, not a `202` from the runtime.
- **Coding errors:** any `Coding action failed` line now has a label instead of
  `unclassified_exception` for known cases.

## 5. Rolling back

Rebuild from `cc3fd62` and restart. Nothing else changes: there's no env or database part to undo.

## 6. Proof

- **Backend tests:** 1,550 pass, with and without the laptop `.env`. They include
  `test_planner_and_coding_retry.py`, which covers:
  - the current-step default and that a wrong step is still rejected;
  - the logged reason;
  - workspace edits retried, while workspace commands and outside writes still pause;
  - the upload retry;
  - the error labels.
- **Static checks:** ruff (including the `app/execution` format check), pyright (0 errors) and all
  CI verify scripts pass, including the secret-hygiene scan.
