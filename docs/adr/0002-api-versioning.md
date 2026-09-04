# ADR 0002 — Version adapters over one canonical domain

Public `/api/v1`, `/api/v2`, and future versions remain thin adapters over canonical domain services. API lifecycle states are current, supported, deprecated, and retired. Business logic is not duplicated per version.
