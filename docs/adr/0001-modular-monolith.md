# ADR 0001 — Start as a modular monolith

## Context
Audoryn needs strong module boundaries but does not yet have scaling evidence that justifies distributed services.

## Decision
Start as one deployable backend with explicit domain and adapter boundaries. Extract services only when independent scaling, isolation, or operational ownership creates measurable value.

## Alternatives
Microservices from day one; single unstructured application.

## Consequences
Development stays fast while future extraction remains possible. Internal boundaries must be enforced through code organization and contracts rather than network calls.
