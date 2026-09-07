# ADR 0023 — Workforce Templates

## Status
Accepted for Foundation 23.

## Context
Audoryn can now create Workers, Jobs, schedules, managed Runs and controlled provider side effects, but SMBs should not need to assemble every role and recurring job from scratch. The Workforce Runtime roadmap requires reusable Worker templates without turning convenience defaults into security authority.

## Decision
- F23 ships six built-in SMB-oriented templates: Customer Support Assistant, Marketing Coordinator, Research Assistant, Sales Assistant, Operations Assistant and Developer Assistant.
- A template may describe a role, responsibilities, example Jobs, required integrations, suggested capabilities, recommended policy rules and approval defaults.
- Template application creates only management records: a reusable Role, one DRAFT Worker bound to an explicitly selected existing Agent Identity, and DRAFT example Jobs whose required capability scopes are currently present in the live tenant catalog.
- If an example Job depends on a missing integration/capability, Audoryn reports the Job as blocked instead of dropping the requirement or weakening the blueprint.
- Templates never connect an integration, declare Agent capabilities, create or enable a Policy, approve an Action, activate a Worker, activate a Job, queue work, create a Run or execute a provider operation.
- Policy recommendations and approval defaults are advisory configuration guidance. Existing deterministic policy and Human Approval remain authoritative.
- F23 exposes the same canonical template domain through API v1 and v2 adapters and a responsive Workforce template picker.

## Failure and persistence boundary
AppDeploy database does not provide multi-record transactions. Audoryn therefore preflights organization permissions, Agent Identity availability, supervisor membership, connected integrations and capability availability before creating template records. Role, Worker and Job mutations continue to use their existing canonical domains and audit events; F23 does not claim transactional all-or-nothing provisioning.

## Consequences
SMBs can start from understandable workforce blueprints while every security-sensitive step remains explicit. F24 can build commercial onboarding and template entitlements on this boundary without turning templates into hidden authorization bundles.
