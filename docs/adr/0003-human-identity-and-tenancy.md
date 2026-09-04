# ADR 0003 — Human identity and tenant membership boundary

AppDeploy authentication establishes the human identity. Organization access is authorized separately through a membership record stored in a user-scoped membership table. Global organization records are fetched only after membership proves the organization ID is reachable by the current user. Roles map to canonical permissions outside API-version adapters.

Human identities and future agent identities remain separate trust domains.
