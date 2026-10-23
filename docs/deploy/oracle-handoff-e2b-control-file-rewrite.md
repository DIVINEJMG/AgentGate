# Oracle handoff: workspace edits after the first one

Written 14 October 2026, for the pull request from `fix/e2b-control-file-rewrite` (the release
after PR #126, `56a1419`). It follows *Audoryn Oracle Update and Deployment Rules — Illustrated
Edition*: inspect first, change only what this release needs, and verify the outcome.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`, image
  `localhost/audoryn-api:latest`.
- **Not touched:** frontend, Vercel, Console, QStash, the database, `/etc/audoryn/audoryn.env`.

## 1. The blocker

| | Finding | Evidence |
|---|---|---|
| Confirmed | Coding task step 8 failed every time at the same point | `Coding action failed … operation=repository.workspace.edit stage=workspace_edit error_type=SandboxException reason=permission_denied`, three times, then the task stopped |
| Confirmed | Cause: the sandbox's file service can create a file in `/tmp` but can't reopen one that already exists there | Reproduced in a sandbox from the same template: the first write of `/tmp/audoryn-step.json` worked, the second failed with `500: error opening file: open /tmp/audoryn-step.json: permission denied`. `/tmp` is sticky and the kernel has `fs.protected_regular=2`. |
| Confirmed | Why it looked random | Every edit reuses the request file `/tmp/audoryn-step.json`, so only the first edit in a sandbox worked. The earlier test (step 4's edit worked, step 5's failed) was this same fault, not a flaky E2B file service as the last handoff guessed. |
| Confirmed | The fix works | Removing the file first and then writing works. A live check of the fixed `E2BCodingRuntime.write` wrote the same `/tmp` file three times and kept the last content. Rewriting a file in `/workspace` still works. |

## 2. What changed (code only)

- **The E2B write retries a refused overwrite:** when the sandbox refuses to overwrite a file
  ("permission denied"), the runtime removes that file and writes it again.
  - This covers the edit request file, the patch file, and the control files that
    initialization writes again on a retry.
  - Any other write error is raised as before.
  - A new file still takes one call, so there's no extra cost.

**No migration, no new dependencies, no env change, no frontend change.** Rebuild the image only.

## 3. Steps (each one only after approval)

1. **Merge** the pull request once CI passes.
2. **Pull:** follow rules sections 3 and 12, checkpoints A and B (`git pull --ff-only origin
   main`). The diff should show only `packages/shared/audoryn_shared/execution/coding/e2b.py`, one test and this doc.
3. **Disk check:** run `df -h /` before building.
   - **If less than 4 GB is free:** run `podman image prune -f` first. It removes only untagged
     images.
4. **Tag, then rebuild:**

```bash
cd /opt/audoryn/AgentGate || exit 1
podman tag localhost/audoryn-api:latest localhost/audoryn-api:rollback-56a1419
podman build -f backend/Containerfile -t localhost/audoryn-api:latest . || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

5. **Restart and verify:**

```bash
systemctl --user restart audoryn-api.service && sleep 20
systemctl --user is-active audoryn-api.service
podman ps --filter name=audoryn-api --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
podman inspect audoryn-api --format '{{.Image}}'
curl -sS -i --max-time 20 http://127.0.0.1:8000/health/ready
curl -sS -i --max-time 20 https://130.61.104.247/health/ready
```

## 4. Checks after deploying

- **Run the coding task again:** "In DIVINEJMG/school, add a README section about setup and a
  small helper script, then open a draft PR."
  - The README edit and the helper script edit should both go through.
  - Then it should ask for your approval to publish the draft pull request.
- **Watch the log:**

```bash
podman logs --since 15m audoryn-api 2>&1 | grep -E "Coding action failed|Planner proposal rejected|Planner model accepted"
```

- **Expected:** no `stage=workspace_edit … permission_denied` lines.
- **Real proof:** the draft pull request on GitHub after you approve it, not a `202` from the
  server.

## 5. Rolling back

Run `podman tag localhost/audoryn-api:rollback-56a1419 localhost/audoryn-api:latest`, then
restart. There's no database or env part to undo.

## 6. Proof

- **Live reproduction** against the coding template, in throwaway sandboxes that were deleted
  afterwards: the overwrite refusal, then the fixed write.
- **Offline test:** `test_e2b_control_file_rewrite.py` checks that the same file can be written
  again, that a new file takes one call, and that other write errors aren't hidden.
- **Coding, E2B and workspace suites:** 85 pass.
- **Static checks:** ruff, the `app/execution` format check and pyright are clean.
