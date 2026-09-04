# AgentGate

**An SOT Product**

AgentGate is the control layer for supervising AI workers inside small and growing organizations. It is built as a security-first modular monolith with a stable canonical core, replaceable provider edges, coexistence-ready API versions, and deterministic authorization as the final authority.

## Current checkpoint

- Foundation 0 — Repository & engineering standards: complete
- Foundation 1 — Application bootstrap: complete
- Foundation 2 — Identity & organizations: complete
- Foundation 3 — Agent identity: complete
- Foundation 4 — Integration framework: complete
- Foundation 5 — Capability model: implemented
- Foundation 6 — Policy engine: next

## Foundation 5

Connected integrations are projected into a canonical capability catalog:

- **Resource** — the external object AgentGate can reach, such as a GitHub repository.
- **Action** — the canonical operation technically available on that resource.
- **Scope** — the stable machine-readable capability string, such as `github.repository.issues.read`.

Agents may declare capability scopes they expect to need. These declarations are descriptive inputs only. They do not grant authority and they cannot trigger provider execution. Foundation 6 introduces deterministic policy decisions.

## Architecture rules

1. Stable canonical domain; replaceable adapters.
2. Public API versions coexist instead of overwriting each other.
3. Security is architecture, not a pre-launch phase.
4. Dangerous actions must be attributable and explainable.
5. AppDeploy is the deployment environment, not the domain boundary.
