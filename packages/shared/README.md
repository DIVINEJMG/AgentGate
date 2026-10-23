# AgentGate shared code (`audoryn_shared`)

The code both AgentGate services use (architecture plan, Phase 4): settings, the database models
and session, the domain, Redis coordination and leases, QStash dispatch and the outbox, the
planner, the providers' setup and catalogues, observability, and the application builder and
lifespan every process starts from.

It imports nothing from the core API (`backend/app`) or the execution service
(`services/execution`). Where things live, and why, is in [service boundaries](../../docs/architecture/service-boundaries.md).

Its dependencies are the libraries every service needs, and the `coding` extra is the E2B SDK.
It is always installed together with a service (see `backend/README.md`), never on its own.
