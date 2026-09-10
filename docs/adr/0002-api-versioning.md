# ADR 0002 — Version adapters over one canonical domain

## Context
Audoryn must evolve without forcing every consumer to migrate immediately.

## Decision
Support public `/api/v1`, `/api/v2`, and future versions through thin adapters that translate to and from one canonical domain model. API lifecycle states are current, supported, deprecated, and retired.

## Consequences
Business logic is not duplicated across API versions. Contract tests become mandatory before retirement or introduction of a public version.
