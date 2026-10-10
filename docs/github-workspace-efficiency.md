# Efficient GitHub execution and workspace recovery

## Runtime boundaries

GitHub API actions use GitHub directly. Only explicit `repository.workspace.*`
actions invoke the coding runtime. API-only tasks do not create a sandbox.
GitHub publication remains a separate governed action with fresh branch state,
canonical verification and reconciliation. Initialization never sends GitHub
credentials to the VM or executes repository setup instructions.

No new dependency or database migration is required. Existing coding-session
`evidence` holds additive initialization metadata and private artifact references.
Do not change the existing 2,000-second complex planner setting for this change.

## Initialization

`CODING_INITIALIZATION_SECONDS=600` is the default persisted deadline, shared
across interruptions and retries. It is independent of the ordinary 90-second
integration action limit. Initialization coordination uses this budget plus a
120-second cleanup margin; checkpoint renewals use the remaining deadline.
The initializing sandbox gets the remaining lifetime plus cleanup margin, then
returns to the configured idle lifetime (300 seconds by default) after readiness.
Initialization counts toward the existing task and daily coding budgets.

Stages: snapshot → validation → sandbox creation → bundle transfer → verification
→ ready. Source is checked for path traversal, symlinks, credentials and size
limits, then packed into deterministic, fast-compressed bundles of at most 4 MiB
of source data. A separate trusted helper checks archive identity, file sizes,
hashes and destinations before accepting workspace readiness.

Snapshot, baseline, manifest and bundles are private task artifacts. Database
checkpoints contain references, stage timing, deadline, ownership generation and
confirmed bundle indexes; no source contents. External initialization calls and
artifact downloads do not retain database transactions.

The same sandbox connection handles transfers and commands within one action.
Sandbox creation is marked requested before dispatch. A lost response requires
metadata reconciliation; an absent ID never permits blind recreation. Uploaded
bundles and extracted files are hash-checked on resume, including bundles whose
acknowledgement was lost. A ready session returns its identity without restoring
baseline content or overwriting edits. Old partially initialized sessions use
conservative sandbox reconciliation and retain their old fingerprint algorithm.

Cancellation, authority changes, lease loss and newer ownership generations
prevent checkpoint acceptance. Deadline exhaustion identifies the unfinished
stage and preserves the baseline and recoverable sandbox state. It does not
reset the deadline or claim rollback.

## Evidence and requests

New sessions use `manifest-sha256-v1`; existing sessions retain their previous
full-content fingerprint. Every export scans relevant files for changes, unsafe
paths and limits. Only changed content crosses the VM boundary. Unchanged binary
files remain in the manifest; binary publication is still unsupported. Generated
untracked caches are excluded while tracked files remain protected.

A saved diff can be reused only after a fresh manifest matches the saved
fingerprint/change set, and publication constraints are evaluated again.
Command completion/output is committed before fallible export checks. Tests are
associated with a published SHA only when the verified change set and tested
source fingerprint agree. Export failure never causes completed commands to replay.

Ordinary workspace actions also release database transactions before E2B or
artifact I/O. A Redis workspace lease and persisted generation fence ownership;
short checkpoints recheck task state and current resource authority before
dispatching mutations and accepting results. Lost ownership cannot overwrite a
newer checkpoint. Known command exit evidence is saved before source verification.
Verification uses a fresh JSON object and an explicit commit so its flags survive
database reload. Inspecting a completed command with missing verification can
rescan its source without launching it again.

Workspace inspection failures enter the existing bounded read-retry path. An
uncertain command launch or mutation remains paused for reconciliation. Local
edits, patches, command launches/cancellation and closure explicitly declare side
effects independently of their GitHub HTTP method; their GitHub API permission
remains `contents:read`. Initialization retains its dedicated resumable stages.
Failure logs include work item, operation, stage and exception type, excluding
raw SDK exception messages, credentials, repository contents and tracebacks.
These changes do not reset existing paused tasks or apply database migrations.

GitHub HTTP connections are reused within one execution pass and closed afterward.
Header/credential state is per request; trusted redirects retain the existing
credential-stripping rules. Small SHA-pinned reads can be reused for 30 seconds,
only within the exact organization/work item/resource/authority version. This
bounded in-memory cache stores at most 32 entries, each at most 30,000 characters.
Branch heads, mutable resources and writes are not cached. Local workspace tools
avoid repeated remote metadata reads; opening and publication still resolve the
repository identity as needed. Current execution authorization is retained.

## Progress and operational checks

FastAPI logs show stage boundaries, elapsed time, source bytes/file counts and
confirmed bundle counts, with sanitized failure categories. Coding status has an
additive compact `initialization` object. Conversation activity uses existing
phase/detail fields for concise source-transfer progress. Coding-panel polling is
15 seconds while active and 60 seconds while idle, pauses while hidden, and has
one in-flight request. The scripted live runner honors persisted retry timing.

Run `docs/sql/github_workspace_diagnostic_neon.sql` in Neon before the next live
test. It reads only compact status fields for the failed scripted task. A NULL
initialization stage on an older session is expected; it is not proof that no VM
was created. Keep its evidence. Use existing authorized cancellation/maintenance
controls to account for occupied sandbox capacity before preparing a new task.
Do not reset old task statuses or delete records to circumvent recovery.

## Verification and live gate

Local tests use fake external services and real local archive extraction/scanning.
Representative fixture: 80 files / 240,000 source bytes use one 2,858-byte
compressed source bundle instead of 80 source uploads (manifest/control-script
traffic is additional). The connection test checks ten writes through one
connection. A scan with 100,000 unchanged text bytes plus an unchanged binary
asset transfers only the edited file's contents and returns under 1,000 bytes
including its manifest. These are fixture measurements, not live performance.

Before live acceptance: review the Neon diagnostic, account for the previous
sandbox, and prepare a fresh authorized scripted work item. Keep normal workers
stopped during scripted execution. Verify interruption/resume, exact tests and
diff, one task-branch commit, one draft PR left unmerged, and results in the
originating conversation. No live model or GitHub/E2B publication is exercised by
the local regression gate. Autonomous planning remains a separate check.
