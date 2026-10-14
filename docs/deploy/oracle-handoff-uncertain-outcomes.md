# Oracle handoff: uncertain writes no longer block, planner summary

Written 14 October 2026, for the pull request from `fix/planner-summary-and-workspace-guard` (the
release after PR #125, `bf6b3b9`). It follows *Audoryn Oracle Update and Deployment Rules —
Illustrated Edition*: inspect first, change only what this release needs, and verify the outcome.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`, image
  `localhost/audoryn-api:latest`.
- **Vercel:** the frontend (new buttons on paused tasks).
- **Not touched:** Console, QStash, the database schema, `/etc/audoryn/audoryn.env`.

## 1. What the live test showed

| | Finding | Evidence |
|---|---|---|
| Confirmed | Sonnet's first plan proposal was rejected with "Planner decision is missing a summary" | `Planner proposal rejected call=1 … reason=Planner decision is missing a summary.` |
| Confirmed | A new coding task waited forever with "Another write on this resource is still in flight…" | Two older actions were still marked as dispatched: a workspace edit (task `c9b5db1d`) and an issue create (task `7c2ad199`). Neither had happened. |
| Confirmed | Nothing in the app could clear such a write | The only way out was a database edit. `resume` refuses uncertain writes, there was no other route or button, and the guard blocked for as long as the record stayed. |

## 2. What changed (code only)

- **Planner:** an `act` proposal without a summary uses its own title instead of being sent back.
  A `finish` still needs its summary, because that is the result.
- **Workspace actions:** they change only their own task's sandbox, so they neither wait for nor
  hold back writes to the shared repository. A repeated edit simply runs again.
- **An uncertain write is looked for again automatically:**
  - The step runs again. Its record says it was dispatched, so it only looks for the write and
    can't send it twice.
  - It looks 3 times, after 1, 2 and 4 minutes (`INTEGRATION_OUTCOME_CHECKS`,
    `INTEGRATION_OUTCOME_CHECK_SECONDS`; the defaults need no env change).
  - If the write is found, the task carries on by itself.
  - Workspace commands are not looked for again, because running them again would repeat them.
- **A person can settle it:** paused tasks on the Jobs page get three buttons:
  - **Check again:** looks for the write once more.
  - **It didn't happen:** runs the step again as a new write.
  - **It happened:** marks the step done and carries on.

  Each choice is recorded in the audit log (`integration.outcome.settled`) and on the action. Only
  the person who asked for the task can settle it.
- **The guard can't block for good:** a write with an unknown outcome holds back other writes to
  the same resource only while its own task is still working on it (running or looking again).
  Once that task is paused for a person, or has ended, other tasks go ahead.

**No migration, no new dependencies, no env change.** The two settings are new but have defaults.
Backend and frontend files changed, so rebuild the image and deploy the frontend.

## 3. Steps (each one only after approval)

1. **Merge** the pull request once CI passes.
2. **Pull:** on Oracle, follow rules sections 3 and 12, checkpoints A and B (`git pull --ff-only
   origin main`). The diff should show only `backend/`, `frontend/` and `docs/` files.
3. **Rebuild** the image (rules section 16):

```bash
cd /opt/audoryn/AgentGate || exit 1
podman build -f backend/Containerfile -t localhost/audoryn-api:latest . || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

4. **Restart and verify** (rules sections 7 and 17): restart `audoryn-api.service`, check both
   `/health/ready` endpoints, and check the running image ID matches the new build.
5. **Deploy the frontend** on Vercel (approval required).

## 4. Checks after deploying

- **The blocked coding task:** after the restart, the old issue-create (task `7c2ad199`) is
  paused, so it no longer holds back the new task. The new task's next try (within about a
  minute) should go ahead.
- **The two old paused tasks:** on the Jobs page, press **It didn't happen** on each. We checked
  that neither write happened. Or cancel them if they're no longer wanted.
- **Planner:** watch for Sonnet accepted with `calls=1`:

```bash
podman logs --since 10m audoryn-api 2>&1 | grep -E "Planner proposal rejected|Planner model accepted"
```

  - **Expected:** no "missing a summary" lines.
  - **If other reasons appear:** send them over.

## 5. Rolling back

Rebuild from `bf6b3b9`, restart, and redeploy the previous frontend. There's no database or env
part to undo. Settled actions keep their recorded outcome, which the older code reads normally.

## 6. Proof

- **New tests:**
  - `test_planner_summary_and_workspace_guard.py`: summary default; workspace actions vs the guard.
  - `test_uncertain_outcome_recovery.py`:
    - a paused write no longer blocks, one being checked still does;
    - the automatic looks happen, back off, then wait for a person;
    - the count starts over for a later step;
    - workspace commands and unknown steps wait for a person;
    - check again, it didn't happen, and it happened each do what they say;
    - only your own paused task can be settled.
- **Nearby suites:** GitHub families, inspection recovery, missing content, integration
  foundation, planner/coding retry, background planning, task activity, delivery watchdog and run
  plans. 197 pass.
- **Static checks:**
  - ruff and the `app/execution` format check: clean.
  - pyright on the changed files: 0 errors.
  - `tsc`: clean.
  - Frontend tests: 156 pass.
  - The full CI run is left to GitHub.
