# Contributing to AgentGate

Use purpose-driven branches such as `foundation/03-agent-identity`, `feature/policy-condition-time`, or `fix/v1-status-adapter`.

Never silently break a supported API version. Add an adapter or new version, migrate consumers, then retire the older contract deliberately.

Once a production migration ships, never rewrite it. Add a new migration.
