# Contributing to Audoryn

## Repository culture

Audoryn evolves through explicit foundations and small reviewable changes.

### Branches

Use purpose-driven branches such as `foundation/02-identity-organizations`, `feature/policy-condition-time`, or `fix/v1-status-adapter`.

### Commits

Prefer conventional, scoped commits such as `feat(api): add v2 status adapter` or `docs(adr): record identity boundary decision`.

### Compatibility

Never silently break a supported API version. Add adapters or introduce a new version, publish its lifecycle state, migrate consumers, then retire the old contract deliberately.

### Database evolution

Once a production migration ships, do not rewrite it. Add a new migration.
