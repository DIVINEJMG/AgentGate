# ADR 0016 — Scheduler and Trigger Engine

## Status
Accepted for Foundation 15.

## Context
Foundation 14 can create durable Work Items only when a human presses Run now. AgentGate Workforce needs recurring and event-driven work without allowing scheduling to become an execution or authorization bypass.

## Decision
- One AppDeploy cron invokes the Workforce scheduler every five minutes, the platform minimum.
- The scheduler is a queue producer only. It never claims Work Items, creates Runs, calls an LLM, invokes the Action Gateway or reaches a provider.
- Schedule registrations live in tenant-scoped trigger configs and a minimal internal scheduler registry. Cron processing reads one bounded page and persists its continuation cursor.
- Recurrence supports daily, weekly and interval schedules with IANA timezone handling.
- Missed occurrences are explicit: `skip` or `queue_once` catch-up.
- Worker working hours are explicit: queue at the scheduled instant or set the Work Item's `scheduledAt` to the next working opening.
- Internal events are authenticated human/system ingress. `job.completed` is the dependency event F16 can emit after a real Run completes.
- External API triggers authenticate with the Job Worker's existing Agent Identity credential. No second trigger secret is introduced.
- Scheduled slots and optional API idempotency keys use deterministic dedupe keys within a bounded Work Item window.
- All trigger types share the F14 readiness checks, Work Item snapshot, correlation ID, audit path and trigger history.

## Consequences
AgentGate can now create work automatically without pretending the Managed Runtime exists. The current KV store cannot provide atomic compare-and-set, so concurrent exactly-once guarantees remain out of scope until Foundation 21 durable execution.
