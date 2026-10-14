# Multi-step execution: saved plans and the coding workspace

Written 14 October 2026, after the first live test. This applies to tasks a worker runs, not to
chat.

**Owner's decisions:**
- the plan goes in a new table (migration 0017);
- coding concurrency stays at 1 per organization and 2 overall;
- a plan has at most 60 steps;
- a run has at most 120 actions, including retries.

## Problems found, and what changed

| # | Problem | Now |
|---|---|---|
| 1 | Runs stopped at 20 actions, so a 20-file job could not finish | With a plan, the limit is 2 actions per plan step: at least 20 and at most 120 (`RUNTIME_MAX_PLAN_ACTIONS`). |
| 2 | No plan; every call re-decided from the whole history | The first planning call also writes the plan. Later calls get the plan, the current step and full evidence for that step and its dependencies only. History older than 12 steps shrinks to one short line each. |
| 3 | A model switch lost the thread | The plan is saved in `run_plan_steps` and is the same for every model. A failed model is replaced mid-step, and the next one continues the same plan. |
| 4 | Files over 10,000 characters could not be written | Edit modes: `replace` (exact find/replace pairs, so only the changed text is sent), `write`, `append` (in chunks at the file's current length) and `delete`. |
| 5 | Every patch ran a full workspace export | Edits and patches save only the files they changed. The full export runs at `diff` and `close`, before publication. |
| 6 | A lost sandbox lost the work | Each changed file is saved outside the sandbox. If the sandbox is gone (killed, or expired after 24 hours), a new one is built at the same commit, every saved change is re-applied, and the step runs again. |
| 7 | No rollback | Every change is a local git checkpoint inside the sandbox, never pushed. `repository.workspace.rollback` returns to the previous checkpoint, the baseline, or any checkpoint returned by an edit. |
| 8 | Waiting tasks retried blindly, then needed attention | A first-come queue per organization: "Waiting for a coding workspace: 1 task ahead." Waiting is bounded by time (2 hours, `CODING_QUEUE_MAX_WAIT_SECONDS`), not by attempts, and is never a failure. |

## How it works

**The plan.**
- **Steps:** each step has a title, goal, tool, target (such as a file path), dependencies and a
  check.
- **Working through it:** each action names the plan step it works on, and whether it completes it.
  Progress is read from the run's own steps, so completing an action never writes to the plan.
- **Changes:** the plan changes only through a revision that gives a reason, at most 3 per run
  (`RUNTIME_MAX_PLAN_REVISIONS`). A revision replaces the steps not yet done.
- **At finish:** the plan's final state is saved, with steps never reached recorded as skipped.
- **In the workspace:** progress shows as "Step 7 of 23: Edit users API (src/api/users.ts)",
  with a progress bar.

**Edits that are safe to repeat.**
- **The helper:** a small helper inside the sandbox (`sandbox_helper.py`) performs each edit.
- **Repeats:** a repeated request finds its work already done. This holds for a replacement whose
  text contains the original, an append at an offset already written, an identical write and a
  re-applied patch.
- **Retries:** an edit whose outcome is unconfirmed is therefore retried. Before, it was paused as
  uncertain. Commands keep their check-before-replay rule.
- **Refusals:** the planner gets a clear reason it can act on, such as "appears 2 times; include
  more surrounding lines" or "is 18000 characters long, not 5".

**Results the planner sees.** After an edit, the planner sees the file's size, hash, a diff
summary and a short preview, never the whole file.

**Also fixed:** a coding wait no longer sets the GitHub account's rate-limit cooldown. That
cooldown was blocking other tasks' workspace actions for 60 seconds.

## Proof

- **[test_run_plans.py](../../backend/tests/test_run_plans.py), 10 tests:**
  - plan checks against the authorized tools, and the 60-step limit;
  - the current step following dependencies;
  - the first decision writing the plan;
  - revisions needing a reason and a remaining budget;
  - every later prompt carrying the same saved plan;
  - evidence focused on the current step;
  - the action limit (20, 60 for 30 steps, 120 at most);
  - progress read from the run's own steps, and the "Step 7 of 23" line.
- **[test_coding_workspace.py](../../backend/tests/test_coding_workspace.py), 15 tests**, with
  the real helper and real git:
  - repeat-safe replace (including the replacement-contains-original case, which the test caught
    as a bug);
  - clear refusals for ambiguous or missing text;
  - chunked appends beyond 10,000 characters;
  - a patch applied once;
  - rollback to the previous checkpoint and to the baseline;
  - paths outside the workspace refused;
  - changed files saved outside the sandbox;
  - a lost sandbox rebuilt with every saved change and a clean checkpoint;
  - queue positions, and admission of the first task in line;
  - the queue wait bounded by time;
  - no account cooldown from workspace waits.
- **Offline benchmark:**
  - **The 12 existing tasks:** identical outcomes. Each now records its plan, with every step done.
  - **Cost on short tasks:** about 7% more AI input tokens (the plan) and 2 to 5 more database
    queries.
  - **Long tasks get cheaper:** the long-run task fell from 39,972 to 39,440 input tokens.
  - **Two new tasks:**
    - `planned-30-steps`: 31 steps, all 30 plan steps done; impossible before;
    - `model-switch`: the first model fails on its third call, and the second continues the same
      plan to completion.
  - **Saved plan in every later call:** in both new tasks, every planning call after the first is
    given the saved plan.
- **Full suite:** all 1,453 backend tests pass; frontend tests, build and the 20 workspace checks
  pass.

## Deploying

- **Migration 0017** (`run_plan_steps`) must be applied before this code runs:
  `python -m alembic upgrade head`.
- **Optional settings:**
  - `RUNTIME_MAX_PLAN_STEPS` (60)
  - `RUNTIME_MAX_PLAN_ACTIONS` (120)
  - `RUNTIME_MAX_PLAN_REVISIONS` (3)
  - `CODING_QUEUE_MAX_WAIT_SECONDS` (7200)
- **Runs already in progress** write a plan at their next planning step and continue under it.
