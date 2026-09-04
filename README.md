# AgentGate

**An SOT Product**

AgentGate is the control layer for supervising AI workers inside small and growing organizations. It is built as a security-first modular monolith with a stable canonical core, replaceable provider edges, coexistence-ready API versions, and deterministic authorization as the final authority.

## Current checkpoint

- Foundation 0 — Repository & engineering standards: complete
- Foundation 1 — Application bootstrap: complete
- Foundation 2 — Identity & organizations: complete
- Foundation 3 — Agent identity: complete
- Foundation 4 — Integration framework: implemented
- Foundation 5 — Capability model: next

## Foundation 4

The first provider adapter is GitHub. Public repositories can be connected without credentials. Private-repository tokens are accepted only when the AppDeploy-backed integration credential vault is configured; tokens are encrypted with AES-256-GCM and are never returned by AgentGate APIs.

A connected integration does not grant any agent authority. Foundation 5 introduces canonical resources, actions and scopes; Foundation 6 introduces policy decisions.

## Architecture rules

1. Stable canonical domain; replaceable adapters.
2. Public API versions coexist instead of overwriting each other.
3. Security is architecture, not a pre-launch phase.
4. Dangerous actions must be attributable and explainable.
5. AppDeploy is the deployment environment, not the domain boundary.
