# ADR 0003 — Human identity and tenant membership boundary

## Context
Audoryn must protect organization data before agent identities or execution are introduced. A user account alone must not imply access to every organization record.

## Decision
AppDeploy authentication establishes the human identity. Organization access is authorized separately through a membership record stored in a user-scoped membership table. Global organization records are fetched only after membership proves the organization ID is reachable by the current user. Roles map to canonical permissions outside API-version adapters.

Human identities and future agent identities remain separate trust domains.

## Alternatives
Unscoped organization listing with post-response filtering; embedding organization IDs directly in auth tokens; treating agents as human users.

## Consequences
Tenant access is explicit and auditable. Membership reads remain bounded per user. Invitations and cross-user membership provisioning can be added later without changing the organization domain model.
