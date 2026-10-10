# Browser capacity and Work Item admission

The managed runtime keeps one Chromium process warm for 180 seconds after its last
context closes. Every governed browser session gets a separate Playwright context;
contexts do not share cookies or page state. The default remains one active context
globally and one per organization. Set `BROWSER_MAX_ACTIVE_SESSIONS` and
`BROWSER_MAX_SESSIONS_PER_ORGANIZATION` only after measuring host memory and
navigation latency at the intended concurrency. The process also checks its local
count; Redis slot leases coordinate capacity across processes. Memory pressure can
still prevent a new context.

QStash wakes the runtime. A PostgreSQL transaction decides whether a due Work Item
is admitted. It ranks the top 1,000 due items by priority plus an age point per
five minutes, with a small penalty for organizations already running work. An item
that loses admission remains queued and gets a delayed wake-up; the runtime sweep
also recovers due work. Capacity waits do not spend the provider failure retry
budget. The Work Item records its waiting reason for the workspace. QStash flow
control caps concurrent runtime callback deliveries at four by default; this is
delivery pressure control, while PostgreSQL still decides admission. Tune
`RUNTIME_QSTASH_PARALLELISM` separately from browser context capacity.
The flow-control key includes the destination, so a local tunnel does not throttle
the production callback endpoint.
The publish headers follow the [QStash flow control API](https://upstash.com/docs/qstash/api-reference/messages/publish-a-message).

A browser session also holds a run-specific Redis lease. Each new owner advances a
fencing generation; session resume renews its leases and refuses to act if a newer
owner has appeared. A lost owner fails closed. The context closes on completed or
failed runs and expires after the configured session TTL, including long approval
waits. The Chromium process closes after the idle period.

Browser contexts live in one Python process. Configure the QStash runtime execute
URL to reach a **single dedicated runtime process** while browser sessions are in
use. A generic load balancer across FastAPI processes cannot resume a context
created on another process. The lease and fence prevent conflicting owners; they
do not transfer an in-memory context. Horizontal browser-worker routing requires a
dedicated ownership-aware dispatcher before increasing runtime replicas.

Queue cancellation requires `jobs.manage` and applies only while the Work Item is
queued. Once admitted or running, an external action may be in progress; stopping
that action needs a separate cooperative cancellation path.
