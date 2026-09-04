# ADR 0001 — Start as a modular monolith

AgentGate starts as one deployable backend with explicit domain and adapter boundaries. Services are extracted only when independent scaling, isolation, or operational ownership creates measurable value.
