# F26 Platform Independence & Python Backend Migration

## F26.0–F26.10
Platform extraction, Vercel frontend boundary, FastAPI skeleton, Render data resources, PostgreSQL canonical state, Alembic schema management, and Redis coordination are established.

## F26.11–F26.15

### F26.11 — Human identity provider boundary
Human authentication now depends on an `IdentityProvider` protocol returning a canonical `HumanPrincipal`. The default provider is deliberately fail-closed until OIDC/SSO configuration is introduced.

### F26.12 — Human vs Agent identity separation
`HumanPrincipal` and `AgentPrincipal` are different immutable domain types. Agent authority is represented by agent identity, credential fingerprint, declared capabilities, risk and policy context. Human sessions are not agent credentials.

### F26.13 — Model provider abstraction
Runtime-facing AI access now depends on a `ModelProvider` protocol. No model vendor SDK appears in domain code.

### F26.14 — Action Gateway
Provider side effects are gated through `ActionGateway`. It validates tenant/agent identity, declared capability and guard outcomes before provider execution. DENY and REQUIRE_APPROVAL cannot execute a provider.

### F26.15 — Integration provider abstraction
GitHub, Gmail, Slack, Google Drive and Google Calendar expose `IntegrationAdapter` manifests containing provider/resource/capability/action/scope/risk/input/output/side-effect/approval-default metadata. Execution remains disabled at this foundation stage; F27/F28 add full provider behavior.

Platform rule: GitHub is source of truth; Vercel hosts frontend; Render hosts backend/runtime; domain code depends on no hosting-platform SDK.
