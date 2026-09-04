# ADR 0011 — Deterministic behavior-aware risk

## Context
Static provider risk does not capture a runaway or repeatedly failing agent. AgentGate needs risk signals before policy evaluation without turning an AI model into the security authority.

## Decision
Foundation 10 computes effective risk from the capability baseline plus bounded recent behavior. Active signals are burst activity (5 requests/10m), repeated blocks (3/30m), repeated failures (2/30m), approval pressure (3/30m), and truncated bounded history. Each raises risk one level, capped at critical. Risk never decreases below the provider baseline.

Decision Lab and Action Gateway policy matching use effective risk. Approved held actions recompute risk during reauthorization. Runtime assessment failure fails closed. A `risk.assessed` audit event is emitted best-effort, while the required pre-provider authorization audit event includes effective risk and active signals.

## Alternatives
- Static capability risk only: rejected because it ignores behavior.
- LLM risk scoring as final authority: rejected because decisions would be harder to reproduce and audit.
- Unbounded action-history scans: rejected because KV reads must remain bounded.

## Consequences
Risk is explainable and reproducible from visible thresholds. The 100-record history window can be truncated, so truncation itself conservatively adds one risk level. Threshold configuration remains code-defined in Foundation 10; organization-specific tuning can be introduced later behind a versioned risk-policy boundary.
