# ADR 0021 — Workforce Performance & Operations

## Status
Accepted for Foundation 20.

## Context
Audoryn now manages Workers, Jobs, Runs, provider Actions, approvals, escalations and incidents. Businesses need to understand whether managed AI work is completing reliably and where operational friction occurs without introducing opaque productivity scoring.

## Decision
- F20 is a read-only analytics projection over existing canonical Worker, Run, Action, Approval, Escalation and Incident records. It creates no parallel execution state.
- Performance is reported through measurable outcomes: completed/failed/cancelled Runs, run duration, approval outcomes, blocked Actions, escalation and incident frequency, department rollups, tool reliability and a recent Worker activity timeline.
- Audoryn does not compute a composite employee quality, productivity or trust score. Analytics are operational evidence for humans, not an authority signal.
- Run success is `completed / (completed + failed)`; cancelled and in-flight Runs are excluded from that denominator.
- Tool reliability counts provider attempts only: `executed / (executed + provider-failed)`. Policy blocks and approval holds remain visible but are never mislabeled as provider failures.
- Escalation and incident frequencies are normalized per 100 managed Runs and may exceed 100 when multiple governance events arise from one Run.
- Every source read is bounded to one page. Truncation is surfaced in the API/UI rather than silently presenting an incomplete sample as an all-time total.
- `performance.read` is read-only and tenant-scoped. F20 adds no mutation API, cron, model call, provider request or authorization hook.

## Consequences
Organizations gain practical workforce operations visibility without weakening deterministic authorization or turning Audoryn into an employee-rating system. Stronger queue/lease durability remains Foundation 21 work.

