# ADR 0025 — Provider-neutral AI gateway

**Status:** Accepted for F31.0-F31.10

## Context

Aduoryn needs an intelligence layer for conversational intent, autonomous planning, and media analysis without making Runtime depend on a single model vendor. F29 remains the provider-neutral execution layer and F30 remains the governed browser layer.

## Decision

Aduoryn introduces a provider-neutral `AIGateway` with model roles:

- `conversation`
- `intent`
- `planner`
- `vision`
- reserved future roles: `embedding`, `reranker`, `safety`

The initial model registry routes conversation, intent, and planner to the coordinator model and vision to the vision model. The initial infrastructure adapter is NVIDIA NIM. Vendor names and model IDs are restricted to configuration/adapter code, never Runtime business logic.

The AI boundary is advisory, not authoritative:

1. AI receives bounded authoritative context and currently available tools.
2. AI returns text or a schema-validated structured proposal.
3. Runtime validates exact resource, capability scope, and structured input.
4. F29 policy/risk/approval/Action Gateway remains the authority for side effects.
5. F30 remains the authority for governed browser execution.

No F31 AI component may directly invoke Gmail, GitHub, Browser, Slack, Drive, Calendar, QStash, or database mutation behavior on behalf of a worker.

## Structured output

AI output is untrusted. Structured decisions are parsed as JSON and validated against JSON Schema. One bounded repair request is allowed for malformed/schema-invalid output. A second invalid response fails closed before Action Gateway.

## Attachment-analysis boundary

Media analysis is exposed through the generic `vision` role. The initial configured model is `nvidia/ising-calibration-1.5-31b`, but Runtime and product code do not depend on that exact model. Future vision specialists can coexist behind the registry.

## Provider failure semantics

Provider failures are normalized into stable categories. Transient failures enter bounded retry/wait semantics. Missing configuration/authentication waits for configuration. Provider failure is not treated as proof that the user's external task failed.

## Observability

Safe invocation metadata is persisted in `ai_invocations`: tenant/object lineage when available, role, provider/model, latency, usage metadata, schema name, request ID, correlation ID, and normalized error category. Prompts, credentials, and secret content are not persisted there.

## Rollout

`AI_ENABLED` is disabled by default. NVIDIA's hosted base URL and model IDs are preconfigured, but the API key is always an external secret. No live model credential is activated by F31 implementation.

Deterministic/manual workflows remain valid when AI is disabled.

## Consequences

- Managed Runtime depends on `AIGateway(role="planner")`, not OpenAI or NVIDIA.
- NVIDIA NIM can be replaced by another adapter without rewriting Runtime.
- Existing F29/F30 governance remains mandatory.
- Conversation persistence and command compilation are deferred to F31.11+.
