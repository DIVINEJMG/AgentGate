# ADR 0015 — Jobs and Work Queue

## Status
Accepted for Foundation 14.

## Context
Foundation 13 introduced business-facing Workers, but Workers still need durable definitions of what work they are responsible for performing. The Managed Runtime does not exist until Foundation 16.

## Decision
- A Job belongs to one organization and one immutable Worker assignment.
- Jobs are revisioned and contain objective, instructions, responsibility links, required capability scopes, priority and completion criteria.
- Job lifecycle is `draft | active | paused | archived`; archived is terminal.
- Required capability scopes are requirements, never grants. Activation/resume and manual queueing revalidate the Worker, bound Agent Identity and active capability declaration.
- Foundation 14 trigger type is manual only. `scheduleId` remains null until the Scheduler/Trigger Engine foundation.
- `Run now` creates a durable Work Item in `queued` state and never calls an LLM, provider adapter or Action Gateway.
- Every Work Item stores the Job revision and an immutable execution snapshot plus a correlation ID.
- Foundation 14 allows humans to cancel only `queued` Work Items. Runtime-owned transitions such as claimed/running/waiting/completed/failed are reserved for later foundations.
- Work Item history is the initial Job execution history; a Run record is not fabricated before the Run Engine exists.

## Consequences
Audoryn can now express and queue repeatable business work without pretending autonomous execution already exists. Foundation 15 can create Work Items from schedules/triggers, and Foundation 16 can claim queued Work Items and turn them into Runs while preserving the original Job snapshot and control-plane boundaries.

