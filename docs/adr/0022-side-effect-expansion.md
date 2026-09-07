# ADR 0022: Controlled side-effect expansion

Status: Proposed

## Decision

Foundation 22 adds bounded provider writes through the existing Action Gateway and AppDeploy database, beginning with GitHub issue creation and Slack message creation.

- The canonical capability is `github.repository.issues.create` and maps to `repository.issue.create`.
- The capability is high risk, requires a connected credential, and always enters the human approval lifecycle before execution.
- Slack message creation uses `slack.messages.create` and `chat.message.create`, with a bounded channel identifier and message text payload.
- The issue payload is normalized by the GitHub adapter, stored with the Action record, included in the approval request, and passed unchanged after reauthorization.
- The idempotency fingerprint includes the canonical payload, so reusing a key with different issue content is rejected.
- Payloads are bounded to a title, body, and up to ten labels; arbitrary fields, URLs, assignees, milestones, and comments are not accepted in this slice.

## Persistence and failure boundaries

No PostgreSQL or new persistence service is introduced. Action, idempotency, and approval records remain on the existing AppDeploy database. Missing credentials, invalid payloads, unavailable capabilities, stale approval authority, and failed audit writes fail closed before the provider write.

## Scope boundary

Email, documents, CRM updates, CMS publishing, and GitHub pull-request writes remain future capabilities. This checkpoint establishes the canonical payload and approval boundary needed before expanding to those providers.
