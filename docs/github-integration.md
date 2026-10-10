# GitHub integration

## Rollout status

Expanded GitHub actions, GitHub events and E2B coding are independently disabled by default. Existing static-token capabilities remain available. No production migration or external GitHub action is part of this implementation.

Deterministic tests establish application behavior, not live GitHub App, OAuth, E2B network isolation or migration readiness. An isolated PostgreSQL upgrade/downgrade and the explicitly authorized live acceptance below remain rollout gates.

## Structure

- `backend/app/execution/providers/native/github/`: catalog, authentication, transport, resources, action families, verification, reconciliation and events. Legacy scopes are exported by the package.
- `backend/app/execution/coding/`: replaceable runtime contract, optional E2B implementation, snapshot validation and durable sessions/commands.
- `backend/app/api/github_routes.py`: compatible v1/v2 setup, readiness, catalog, coding status, diff and cancellation APIs; root OAuth callback.
- Shared credential, task, action, approval, conversation and outbox infrastructure stays provider neutral. PostgreSQL owns durable state; Redis coordinates admission, renewal and account requests.
- Migration `0010_github_coding` adds installation/onboarding bindings, aliases, action evidence, coding sessions/commands/budgets and delayed outbox delivery. It follows `0009_integration_foundation`.

The [action catalog](github-actions.md) lists inputs, permission requirements and approval defaults. It is generated from the same catalog used by the planner and public action listing. From the backend directory, regenerate it with `python scripts/generate_github_catalog.py` and the backend dependencies available on `PYTHONPATH`.

## Local configuration

Keep secrets in the ignored backend `.env` or the backend secret manager. Never put tokens, signing keys or callback codes in source, planner context or sandbox environment variables.

```dotenv
INTEGRATION_FOUNDATION_ENABLED=false
GITHUB_EXPANDED_ENABLED=false
GITHUB_EVENTS_ENABLED=false
CODING_EXECUTION_ENABLED=false
GITHUB_API_VERSION=2026-03-10
GITHUB_APP_ID=
GITHUB_APP_SLUG=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITHUB_APP_PRIVATE_KEY=
GITHUB_WEBHOOK_SECRET=
GITHUB_CALLBACK_URL=
GITHUB_FRONTEND_URL=http://localhost:5173
INTEGRATION_ENCRYPTION_KEY=
E2B_API_KEY=
CODING_TEMPLATE_ID=
CODING_ALLOWED_HOSTS=registry.npmjs.org,pypi.org,files.pythonhosted.org
```

Registering or installing a GitHub App and creating an E2B template require separate authorization. Configure a public HTTPS callback ending `/integrations/github/callback` and the configured webhook route. Request only the action catalog permissions actually needed. Projects require their separate organization permission and exact Project grant. Installation access alone is insufficient: the connection owner's GitHub access is rechecked before actions.

Configure the credential vault before starting authorization. GitHub readiness requires
`INTEGRATION_ENCRYPTION_KEY`; a missing key blocks onboarding before exchanging the
single-use OAuth code. Preserve an existing key: replacing it makes previously encrypted
credentials unreadable. Restart FastAPI after changing local configuration. If a callback
fails after exchanging its code, start a fresh connection from the GitHub card instead of
reloading the callback URL. The card also opens connection access/resource controls and
removes Audoryn connections without uninstalling the GitHub App.

The optional backend dependency is `.[coding]` (`e2b==2.52.1`). Without the SDK, key or pinned template, coding tools are omitted; GitHub API tools continue working. Use an immutable E2B template containing explicitly pinned Python, Node and Git versions. Record those versions and template identity in the live acceptance evidence; an arbitrary configured template is not proof that a build toolchain is supported.

Review connection ownership and legacy scope-only grants, apply migrations to an isolated database first, and review exact resource bindings before enabling the foundation locally. New App onboarding does not require an existing token connection. OAuth state is single-use, user/organization-bound and expires after 15 minutes. Selecting an installation verifies access server-side. Reconnection keeps the connection identity and sharing records, rechecks the same GitHub user and installation, and requires pending work to revalidate authority. Disconnect revokes connection credentials, not a shared installation.

## Execution and authority

Worker creation and chat use the shared compiler. Several repositories may be bound to one objective; missing or ambiguous resources remain a clarification/setup wait. Chat tasks are asynchronous, deduplicated and report to the originating conversation. The planner chooses tools from authorized observations; repository and event content cannot grant permissions or dictate backend execution.

Routine granted issue/comment/task-branch/draft-PR/Project actions do not repeatedly request approval. Merges, formal approving/request-changes reviews, CI dispatch/rerun/cancel, release publication, workflow-file edits and content deletion require exact approval. Organization policy can impose additional approval. Payload, destination, revision and authority changes are revalidated. Default/protected branch writes, force pushes and protection bypass are rejected.

Writes are reread against canonical objects. Durable markers and object identities support reconciliation; matching titles alone do not. Unknown outcomes pause rather than blindly replaying a write. Completed steps survive retries; partial completion identifies failed and remaining work. An accepted workflow dispatch is not a passed test; published checks require exact recorded revision/command evidence.

GitHub transport is installation-scoped: requests serialize, writes have one-second spacing, throttling follows provider headers, and five consecutive transport/5xx failures open a two-minute cooldown. Permission/validation failures do not open that breaker. Delayed outbox retries allow healthy deliveries to proceed. Coding uses a separate capacity partition, so E2B failures do not block issue management or other providers.

## Coding limits and evidence

Defaults: two global workspaces, one per organization, 30 active minutes per task, 120 organization minutes per UTC day, five idle minutes before pause and 24 hours maximum retention. Environment settings can change these budgets. Cross-day resumption reserves remaining time against the new day. Creation is persisted before external I/O; unknown creation is recovered by exact session metadata, never by launching another sandbox blindly.

Source snapshots are pinned to observed commit SHAs. Backend credentials never enter the sandbox. Outbound access is limited to configured dependency registries, inbound public traffic is disabled, and exported changes reject unsafe paths, symlinks, credential patterns and excessive size. Binary assets can be retained unchanged; binary publication is intentionally unavailable. Commands produce sanitized output, actual SDK exit evidence and source fingerprints. Lost process evidence is uncertain, not a successful test.

Publication uses backend GitHub tools, one atomic commit with the recorded parent SHA, and a non-force branch update. A moved branch requires new observation/replanning. Cleanup exports evidence before pause/deletion where possible. Export failure is recorded and does not authorize publication or prevent other session cleanup. Keep the outbox recovery worker running for retention cleanup.

## Events

Enable event intake separately. HMAC verification precedes parsing trusted installation identity. Explicit installation bindings route each tenant's delivery to its configured authorized standing jobs. Duplicate deliveries do not create duplicate work. Suspension/removal invalidates affected authority; a later webhook does not silently restore it. Known self-generated events require object/action correlation, not bot identity alone.

Supported intake families are installation/access changes, issues/comments, PRs/reviews/comments, push, workflows/checks and releases. Project event intake remains unavailable until its verification/resource routing is implemented. Events without a proven action correlation must still respect explicit subscriptions and standing grants.

## Troubleshooting

| State | Action |
|---|---|
| Setup unavailable | Check the independent flags, App configuration and callback URL; do not report a simulated connection. |
| Reconnect required | Reauthorize the same owner/installation, rediscover resources, then resume after exact grant review. |
| Rate limited / cooldown | Honor the recorded retry time. Other provider/account work may continue. |
| Approval wait | Review exact proposed payload/repository/revision; changed content needs revalidation. |
| Uncertain write or command | Inspect durable evidence/canonical state. Do not retry an unverified mutation. |
| Branch moved | Observe current head and diff, then replan; never force-push. |
| Workspace capacity/budget wait | Inspect active/paused sessions and budget settings; GitHub API actions remain independent. |
| Missing test result | Inspect command exit, source fingerprint and tested revision; a start receipt is not success. |

## Acceptance and maintenance

Before enabling affected features, use an explicitly authorized test repository, App installation and E2B account: natural-language creation, shared exact binding, chat code task, snapshot/edit/test, atomic branch publication, draft PR, interrupted recovery and one verified originating-conversation outcome. Separately exercise merge/release/CI approvals and revoked access. Verify real E2B egress rules and cleanup against the configured template. Run actual migration upgrade/downgrade on an isolated PostgreSQL database. No production migration, push or deployment follows automatically.

For a new action: add one typed catalog entry, narrow permissions and inputs; implement its family adapter, exact resource/path/revision checks, canonical verification and uncertain-write reconciliation; classify retries/approval; add deterministic failure and authorization tests; regenerate the catalog; run compatibility checks; document evidence limits; keep it disabled until required live checks pass. Never add unrestricted HTTP/GraphQL tools.

Primary references: [GitHub REST versioning](https://docs.github.com/en/rest/about-the-rest-api/api-versions), [installation token controls](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app), [request guidance](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api), [E2B persistence](https://docs.e2b.dev/sandbox/persistence), [E2B network controls](https://docs.e2b.dev/network/internet-access).
# Coding workflow recovery

## Long planning and recovery

`RUNTIME_PLANNER_TIMEOUT_SECONDS=1500` gives each planning cycle up to 25 minutes,
including its preparation and AI response. The runtime's AI HTTP timeout uses this
budget; chat retains `AI_TIMEOUT_SECONDS`. QStash delivery stays short: the signed
callback acknowledges acceptance and starts a tracked backend task. PostgreSQL is
authoritative; task identity, run identity and phase are committed before planning.

Background concurrency retains the configured runtime parallelism through shared
Redis slots. Work-item and capacity leases renew every 30 seconds with a 90-second
expiry. Duplicate deliveries do not execute concurrently. Lost coordination cancels
the owner. Shutdown cancels tracked tasks before providers shut down; a process
crash leaves durable work for the existing scheduled recovery sweep after leases
expire. The sweep must remain configured and the backend must be available.

Tool preparation and AI response have separate elapsed-time logs and `plannerTiming`
fields in v1/v2 run APIs. Preparation commits and releases its database transaction
before inference. Planner invocation telemetry uses separate short transactions.
Cancellation/error recovery rolls back and reloads the work item by tenant and ID
before saving retry/wait state. AI waits are reported to the originating conversation
without requiring another AI call. Completed action checkpoints remain unchanged.

This is a per-cycle planning budget, not a maximum job duration or an unlimited
command timeout. Existing command, action, cost and retry budgets remain enforced.
Verification with mocked providers does not establish live 25-minute model readiness.

Task compilation expands catalog-declared read prerequisites on the exact selected
repository, subject to current account access and provider consent. It does not
select the worker's next action or grant additional writes. Tree reads, task branch
creation and coding workspace opening retain their pinned SHA requirements; the
planner obtains an observed SHA through the authorized branch reader.

The planner validates the complete input schema before checkpointing. Previously
saved invalid inputs are classified before dispatch, recorded as failed attempts,
and returned to planning through the outbox. Correction attempts use the configured
runtime retry budget. Exhaustion produces an attention result with completed work
preserved, rather than leaving a pending invalid step running indefinitely.

Provider evidence stays structured. Bounded observations explicitly identify
omitted content; workspace file reads support offset/maxChars pagination. A worker
must read additional segments before claiming it inspected omitted content.

Export excludes recognized generated caches only when they were absent from the
original source snapshot. Tracked files remain subject to validation. Source safety
checks still block unsafe paths and credentials. Publication authority is checked
separately: an unauthorized source change remains visible in the saved diff and
prevents publishing, while finished commands retain their output and actual exit
status. Failed export never authorizes command replay or a verified tested revision.

Run APIs expose the wait reason and scheduled retry time. The job workspace shows
correction/retry status and offers planning retry for an AI wait. Existing queued
deliveries resume saved steps after backend restart; submit a fresh conversation
task to compile prerequisite grants for an older task whose immutable grants lack
them. Live GitHub/E2B verification remains a separate acceptance check.
# Missing content and read recovery

`github.repository.contents.read` inspects exactly one `path`. Step descriptions and progress
labels reflect that typed path; requesting several files requires separate planner actions.

A contents 404 triggers read-only repository and revision access checks, followed by a path lookup
pinned to the observed commit SHA. Only a confirmed 404 at that accessible revision returns
`exists: false`, the path and revision, and explicit coverage limits. An inaccessible repository or
revision, denied permission, or transient error is never presented as confirmed absence. This
observation authorizes no creation: the planner chooses its next action under the existing grants.

Non-browser integration reads use the shared bounded retry/replanning handler after dispatch,
as well as during preparation. Failed actions remain recorded and cannot be repeated past their
retry budget. Permission denials and uncertain writes retain their existing blocking/reconciliation
paths; this change does not make writes safely replayable or alter browser execution.
