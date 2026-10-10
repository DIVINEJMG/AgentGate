# Scripted GitHub workflow verification

This is a development tool, not a production planner or a model benchmark. It
supplies typed decisions and source files, then requires observed results before
progressing. It does not infer coding decisions, create workers, interpret chat,
prepare new task grants or establish live AI readiness.

## Controlled inspection recovery

For an existing scripted task paused as `uncertain_outcome` during a workspace
inspection, repeat its original live command with `--resume-inspection`. Keep the
same organization, work-item, resource, manifest and publication choice. Normal
backend/runtime workers must remain stopped. The runner retains the same run and
current step, validates the exact saved fixture, rejects unscripted history and
other unresolved actions, and rechecks current authority under a runtime lease.
An audit entry records the explicit recovery and previous reason. Recovery is
limited to five human requests and cannot resume an uncertain edit, patch,
command launch, sandbox creation/deletion or GitHub write.

Missing verification for a completed command is refreshed through a governed
command-status inspection; the original command is not launched again. If source
verification fails or its fingerprint changed, publication stops. Existing
completed actions remain intact. No SQL status reset is needed. After interruption
while the task is running, the same command resumes without another recovery entry.
The runner prints structured runtime diagnostics to stderr and result JSON to stdout.

## Script files

- `backend/scripts/simulate_github_workflow.py`: offline/live command-line runner.
- `backend/scripts/github_scripted_planner.py`: supplied decisions validated by
  the existing adaptive planner validator.
- `backend/scripts/fixtures/github_workflow/plan.json`: ordered actions and
  references to observed IDs, revisions and outputs.
- The two adjacent Python files are proposed source fixtures. They are copied
  into the isolated test workspace; they do not modify the main checkout's
  `backend/scripts/check_timezone_data.py` or its corresponding test file.

## Start with offline mode

Run in PowerShell, from the backend directory. Use the existing runtime packages:

```powershell
cd C:\Users\divin\AgentGate\backend
$env:PYTHONPATH = Join-Path $env:LOCALAPPDATA 'Audoryn\python313-packages'
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py
```

Offline is the default. It makes no GitHub, E2B, AI, Redis or Neon calls. It uses
fake GitHub state, the real action schemas, planner input validation, the Action
Gateway with a fixture guard, and real path/branch safety checks. It runs the
supplied standard-library tests in a local workspace with a restricted child
environment and no shell. This mode does **not** exercise the managed runtime,
database policies, GitHub transport, coding service or E2B isolation.

Results and checkpoints are under `.local/github-simulation/`, already ignored
by Git. A filesystem lock prevents concurrent offline runners using one directory.
Fake commit identities and `https://example.invalid/...` links are simulations.
The tests' command, output and exit status are actual local results.

### Interrupted recovery

```powershell
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --state-dir .local/github-interruption --stop-after 7
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --state-dir .local/github-interruption
```

The first command exits `2` intentionally at a saved checkpoint. The second
continues it. Running again after completion does not repeat completed writes.

### Lost write response and exact reconciliation

```powershell
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --scenario uncertain-write --state-dir .local/github-uncertain
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --scenario uncertain-write --state-dir .local/github-uncertain
```

The first pauses after a fake commit succeeded but its response was lost. The
second looks up the fake provider ledger by exact durable identity and payload
fingerprint before continuing. The final fake write count is three: branch,
commit, PR. This establishes fixture reconciliation, not live adapter readiness.

### Approval pause and branch movement

```powershell
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --scenario approval --state-dir .local/github-approval
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --scenario approval --state-dir .local/github-approval --approve-fixture
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --scenario branch-race --state-dir .local/github-race
```

`--approve-fixture` changes only the offline guard. It cannot approve live actions.
Branch movement pauses before a commit/PR; no force-push or rollback is attempted.
Use a new directory for a new scenario or changed fixture. Missing target files
are the default fixture. Exit `0` means fixture completion; exit `2` means a
checkpoint stop or a blocker that needs inspection.

## Controlled live mode — run only when explicitly authorized

Before another coding run, inspect the previous workspace with
`docs/sql/github_workspace_diagnostic_neon.sql`. Account for its sandbox capacity
using normal authorized recovery/cleanup; do not reset the old task. See
`docs/github-workspace-efficiency.md` for the 600-second shared initialization
deadline, source-bundle checkpoints and the separate live acceptance gate.

### Prepare without AI

Stop the backend, runtime, scheduler and outbox workers first. Preparation writes
only application database records, never calls inference, GitHub or E2B, and
does not enqueue runtime execution. Normal workers must remain stopped because
the new item is queued and normal recovery can otherwise pick it up.

Use an existing authorized work item as lineage. Its job, running state and
completed steps are untouched. The CLI resolves the exact repository through
its active grant, checks current access and initiating-user permissions, and
creates a separate job/revision, conversation request and request-only grants.
Missing capabilities are reported; this mode never adds authority to fix them.
Existing destination/path constraints are preserved and rechecked at execution.

```powershell
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --mode prepare --organization-id YOUR_ORGANIZATION_UUID --source-work-item-id YOUR_AUTHORIZED_SOURCE_UUID --preparation-key timezone-live-1 --workers-stopped --publish
```

`--publish` records the fixture choice for the subsequent live run; preparation
does not publish. Omit it for testing without publication. Repeat the same key
to retrieve the same work item safely. Changed fixture/publication settings
require a new key. Use the printed `workItemId` and `resourceId` in live mode,
with the same manifest and publication choice. The subsequent live command
still requires `--allow-coding` and, when publishing, `--publish`.

No SQL migration is needed. This is a development CLI with backend database
access, not a new unauthenticated public endpoint. It retains the source user
identity and requires that user's current create/run and connection authority.

Live execution consumes E2B usage, changes database checkpoints and may write to
GitHub. Creating the script does not perform or authorize a live run. Keep normal
backend/runtime/outbox workers **stopped for the entire scripted run and resume**:
they use the normal AI planner and must not take over this fixture work item.

Use a fresh, already authorized **queued work item**, tied to the desired
conversation and immutable job revision. Its grants must contain the exact
repository, paths and tools in the manifest. The script refuses other tenants,
other repositories, changed fixture fingerprints and existing unscripted actions.
Worker creation/chat interpretation/task preparation occur separately through
normal application controls; this script replaces execution planning only.

Use read-only SQL to find IDs; select no payloads, credentials or file contents:

```sql
SELECT id, status, created_at
FROM work_items
WHERE organization_id = '11e0f443-e0c7-43bc-bbf1-74062069bd30'
ORDER BY created_at DESC LIMIT 10;

SELECT id, display_name, configuration->>'repository' AS repository
FROM integration_resources
WHERE organization_id = '11e0f443-e0c7-43bc-bbf1-74062069bd30'
  AND provider = 'github' AND resource_type = 'repository';
```

First verify inspect/edit/test/diff **without GitHub publication**:

```powershell
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --mode live --organization-id YOUR_ORGANIZATION_UUID --work-item-id YOUR_FRESH_WORK_ITEM_UUID --resource-id YOUR_AGENTGATE_RESOURCE_UUID --allow-coding --workers-stopped
```

This ends with an attention result explaining that publication is disabled. It
does not turn that partial outcome into full completion. For the complete test,
use a separate fresh authorized work item and add `--publish` from the start:

```powershell
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --mode live --organization-id YOUR_ORGANIZATION_UUID --work-item-id YOUR_FRESH_WORK_ITEM_UUID --resource-id YOUR_AGENTGATE_RESOURCE_UUID --allow-coding --workers-stopped --publish
```

No new credentials or environment placeholders are required. Existing GitHub
App, coding runtime, vault, Redis, database and artifact configuration are used.
The script changes Smart Multi-AI routing only inside its own process, disables
its automatic outbox-drain HTTP trigger, and does not modify `.env` or the server.
It drives the real managed runtime, which retains resource authorization,
policy/approval checks, durable action evidence, provider verification and
originating-conversation messages. Per-step Redis leases use the normal runtime
keys. Approval waits stop the runner: approve the exact action through the normal
UI, stop workers again, and resume the same command. Never use offline approval
flags for live authority. Revoked grants and uncertain writes use normal recovery
controls; the script does not reopen failed or blocked work items blindly.

Existing target files are inspected at the observed SHA. If their content differs
from the supplied proposal, the fixture pauses rather than replacing them. Review
them and use a revised fixture with a fresh authorized work item. Publication
requires passing tests, unchanged source evidence, matching diff fingerprint and
changes limited to the fixture files. Default/protected branch protection stays
in the backend. The result verifies an open draft PR at the reread branch SHA;
it never requests merging.

`--stop-after N` also works live; keep workers stopped and resume with the same
IDs, manifest and publication option. `--max-steps` and `--poll-seconds` bound the
driver (defaults 100 steps and two seconds); command status may require repeated
polls. Saved checkpoints remain in Postgres, not an offline journal.

Inspect coding sessions and close the workspace using normal authorized controls
after testing; normal retention cleanup still applies. Inspect queued outbox work
before restarting normal workers after an interrupted scripted run. No production
migrations, sandbox purchases or deployment are part of this tool.

## Controlled inspection recovery

After fixing an inspection defect, `--resume-inspection` can reopen the exact saved
scripted task in `uncertain_outcome` or `partial_completion`. For a partial task it
requires exactly one failed workspace inspection, all preceding steps completed,
and no subsequent action. The same fixture, resource, run and action identity are
required. Current grants and ownership are checked again; unresolved other actions
block recovery. Five explicit recovery requests are the maximum.

The failed inspection and prior result remain in recovery history. Completed edits,
commands and finish records are preserved. Only the failed inspection receives a
fresh bounded retry sequence. Completed tests with unavailable source verification
can receive one fresh status inspection, without launching the command again.
Publication still requires verified unchanged source and matching diff evidence.

Backend baseline manifests are reused when their remote SHA-256 matches. Failed
upload acknowledgements are reconciled through an independent remote hash check.
Logs distinguish artifact reads, manifest checks, uploads, reconciliation and
verification, with allowlisted SDK reasons and HTTP status; raw errors are excluded.

Keep workers stopped and use the exact existing IDs and publication option:

```powershell
uv run --python 3.13 --no-project python scripts/simulate_github_workflow.py --mode live --organization-id YOUR_ORGANIZATION_UUID --work-item-id YOUR_EXISTING_WORK_ITEM_UUID --resource-id YOUR_AGENTGATE_RESOURCE_UUID --allow-coding --workers-stopped --publish --resume-inspection
```

This performs live authorized execution. Local implementation tests do not prove
that an old sandbox is still accessible; expired or missing sandboxes remain blockers.

## Verification limits

Offline regression tests demonstrate local fixture behavior. A live run has not
been performed during implementation. A passing live run proves this supplied
sequence works through the execution pipeline; it does not prove autonomous model
selection, natural-language preparation or production reliability.
