# AgentGate

**An SOT Product**

AgentGate is the control layer for supervising AI workers inside small and growing organizations. The product is being built as a security-first modular monolith with replaceable integration edges, coexistence-ready API versions, and deterministic authorization as the final authority.

## Current checkpoint

- Foundation 0 — Repository & engineering standards: started
- Foundation 1 — Application bootstrap: deployed
- Foundation 2 — Identity & organizations: complete
- Foundation 3 — Agent identity: implemented
- Foundation 4 — Integration framework: next

## Architecture rules

1. Stable canonical domain; replaceable adapters.
2. Public API versions coexist instead of overwriting each other.
3. Security is architecture, not a pre-launch phase.
4. Dangerous actions must be attributable and explainable.
5. AppDeploy is the deployment environment, not the domain boundary.

## Development sequence

Each foundation is implemented, tested, reviewed, and checkpointed before the next foundation begins.
