# ADR 0017 — Managed Agent Runtime

## Status
Accepted for Foundation 16.

## Context
Foundation 15 can create durable Work Items automatically, but no component owns claiming, planning, Run creation, ordered execution, approval waiting, recovery or completion propagation. The runtime must add useful autonomy without becoming a privileged path around Audoryn security.

## Decision
- The Managed Runtime consumes the F14/F15 Work Queue through a small global dispatch index; the tenant-scoped Work Item remains the source of truth.
- A claim uses a random lease token, persisted lease expiry and immediate re-read verification. The current KV store has no compare-and-set primitive, so Foundation 16 explicitly does not claim strong exactly-once execution under a simultaneous race.
- Every claim creates one durable Run and up to six ordered Run Steps.
- Planning and internal reasoning use bounded `ai.run`. The model receives no provider credentials and no direct external-action tools.
- Planner output is validated against the bound Agent's current active capability/resource catalog before it can become executable steps.
- External action steps call the existing Action Gateway through a trusted server-side Agent Identity principal. The gateway still performs capability, risk, deterministic policy, approval, audit, incident and provider checks. Managed Runtime never stores or reconstructs the Agent's one-time plaintext credential.
- `REQUIRE_APPROVAL` moves the Run and Work Item to `waiting_approval`. Existing approval execution remains authoritative; the runtime later observes the held Action result and continues only after it becomes executed.
- Completion criteria are assessed from recorded step observations. Successful Runs mark the Work Item completed and emit Foundation 15's `job.completed` event for dependency Jobs.
- AI transport failures may requeue the same immutable Work Item with a bounded retry count of two. Policy blocks, approval rejection and completion-criteria failures are not automatically retried.
- The existing five-minute scheduler cron remains non-AI. Managed Runtime autonomy uses a separate hourly cron and starts at most one AI Run per cron tick; humans with `jobs.run` may execute a due Work Item immediately.

## Consequences
Audoryn now has a real managed execution loop while preserving the control plane as the authority boundary. Strong transactional claiming, distributed leases and write-side exactly-once guarantees remain deferred to the later durable-execution foundation.

