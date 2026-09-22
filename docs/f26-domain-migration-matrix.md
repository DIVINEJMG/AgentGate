# F26 domain migration matrix

Migration is behavior-first, not file-for-file. The TypeScript reference remains available until each
domain reaches parity.

| Order | Domain | Python status at F26.25 |
|---:|---|---|
| 1 | System / bootstrap | foundation migrated |
| 2 | Authentication | boundary migrated |
| 3 | Organizations & memberships | repository/schema foundation |
| 4 | Agent Identity | schema/principal foundation |
| 5 | Workforce | schema only |
| 6 | Jobs | schema + queue foundation |
| 7 | Integrations | manifests/boundary migrated |
| 8 | Capabilities | schema foundation |
| 9 | Policies | schema foundation |
| 10 | Risk | schema foundation |
| 11 | Action Gateway | foundational runtime path migrated |
| 12 | Approvals | schema foundation |
| 13 | Incidents | schema foundation |
| 14 | Audit | schema foundation |
| 15 | Scheduler | process foundation migrated |
| 16 | Runtime queue | PostgreSQL claim/checkpoint foundation |
| 17 | Runtime | execution disabled pending migration |
| 18 | Memory | schema only |
| 19 | Artifacts | storage abstraction foundation |
| 20 | Results | schema only |
| 21 | Commercial | pending |
| 22 | Supervision / performance | pending |

A domain may be marked migrated only when its API contracts, security invariants, persistence behavior,
and parity tests are satisfied.
