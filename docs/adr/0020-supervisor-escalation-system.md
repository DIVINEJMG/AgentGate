# ADR 0020 — Supervisor & Escalation System

## Status
Accepted for Foundation 19.

## Context
Managed Audoryn Workers can now plan and execute bounded work through the existing control plane. Autonomous work still needs a normal human operating model when a Job fails, policy demands intervention, or effective risk becomes elevated.

## Decision
- Every managed Worker continues to carry one validated human `supervisorUserId`; F19 makes that relationship operational by routing escalation records to the supervisor by default.
- Escalations are durable, tenant-scoped supervision work. Default deterministic triggers are final Job failure, policy decision requiring human attention, HIGH risk and CRITICAL risk.
- Organization rules may disable an escalation class or raise its attention severity, but risk-derived severity cannot be configured below its safe floor.
- Escalation creation never authorizes an action. Provider execution remains exclusively behind capabilities, deterministic policy, risk, approvals, audit, incident controls and the Action Gateway.
- Supervisor actions are restrictive or administrative: acknowledge/resolve, add notes, reassign the escalation, cancel a Run, pause a Worker, or create an F11 Incident.
- Cancelling a Run persists a correlation-level cancellation guard. The Action Gateway checks that guard on fresh managed actions and again during approval-time resume, so a cancelled waiting Run cannot later reach the provider through a stale approval.
- Worker pause does not mutate Agent Identity or erase credentials. Existing F11 incidents remain the authority for organization/agent/integration kill switches.
- Push delivery is best-effort. The durable escalation inbox is authoritative even when a supervisor has no push-capable device.
- F19 does not claim real-time preemption of a provider call already in flight. It prevents subsequent managed steps and future approval-time execution.

## Consequences
Human supervision becomes part of normal autonomous work without creating a second authorization plane. Performance analytics remain deferred to F20 and strong queue/lease atomicity remains deferred to F21.

