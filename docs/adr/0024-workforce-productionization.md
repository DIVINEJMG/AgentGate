# ADR 0024 — Workforce Productionization

## Status
Accepted for Foundation 24.

## Context
F13–F23 established managed Workers, Jobs, durable execution, workplace tools, supervision, performance, controlled side effects and SMB templates. The product now needs commercial capacity controls, onboarding, collaboration and production visibility without allowing billing state to become security authority.

## Decision
- Audoryn introduces a server-side commercial plan catalog with Free, Team, Business and Scale worker-capacity entitlements.
- Worker creation enforces the current verified plan server-side. Existing Workers are never silently disabled when a plan state changes; new expansion fails closed when capacity cannot be verified.
- A `BillingAdapter` boundary reads subscription state. The initial adapter uses AppDeploy persistence and defaults to Free when no verified external billing record exists. No checkout or paid upgrade is fabricated.
- Job and Run usage is accounted from canonical records with a supplemental bounded usage ledger for commercial events. Truncation is surfaced instead of hidden.
- Organization invitations use AppDeploy authenticated invite codes with an email allow-list. Invite joining creates the existing Audoryn human membership; it never grants Agent capability or policy authority.
- Commercial onboarding includes security contact, integration, policy, Worker, Job and first completed Run. F23 template use is available as an optional accelerator.
- Production monitoring projects dead-letter Work Items, failed Runs/Actions, escalations and critical Incidents from canonical records.
- F24 does not add a second authorization path. Commercial entitlements limit product capacity only.

## Billing boundary
There is no payment provider connected in this checkpoint. The billing adapter contract is production-shaped, but a non-Free plan becomes authoritative only when a future provider adapter persists verified server-side subscription state. Organization users cannot self-upgrade by changing client data.

## Security invariant
Billing answers **how much of the product an organization may provision**. The existing control plane still answers **whether an AI action may execute**.
