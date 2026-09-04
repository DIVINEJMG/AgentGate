# ADR 0013 — Productization and billing boundary

## Context
AgentGate now has a complete security control-plane foundation, but a commercial product also needs workspace configuration, onboarding, usage visibility, plans, documentation and a safe path to future billing.

## Decision
Foundation 12 adds a canonical productization domain. Workspace settings are tenant-scoped and owner/admin managed. Readiness and usage are derived from bounded AgentGate records rather than fabricated analytics. API v1 and v2 expose version adapters over the same productization service.

The initial plan catalog is Free, Team, Business and Scale. Billing is intentionally `observe_only`: no payment provider is connected, no checkout is exposed, and plan limits are advisory. Existing organizations are not silently restricted by a pricing catalog.

A future Stripe/Paddle/other adapter must implement a replaceable billing-provider boundary, persist provider subscription identifiers server-side, validate webhook state, and only then transition entitlement enforcement from observe-only to enforced.

## Safety semantics
- Product settings never weaken runtime policy, risk, approval or incident controls.
- Workspace writes require `organizations.manage`; reads require tenant membership.
- Usage reads are bounded and expose truncation rather than claiming exact totals when the window is incomplete.
- No billing secrets or payment credentials exist in frontend code or repository state.
- Plan state cannot authorize agent actions.
