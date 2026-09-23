# F29.0–F29.10 — Universal Execution & Capability Foundation

## Status

This document freezes the post-F28 product/API baseline and defines the first F29 execution boundary.

F29.0–F29.10 preserve the existing public `/api/v1` and `/api/v2` product contracts. The migration changes how execution providers are represented and selected; it does not intentionally remove or rename existing product endpoints.

## Authoritative execution path

```text
Worker
  -> Job / Objective
  -> Planner
  -> Capability Resolver
  -> Action Gateway
  -> Policy + Risk + Approval
  -> Execution Resolver
       -> Native API Provider
       -> Browser Provider (reserved for F30)
       -> MCP / Custom Provider (reserved for later F29)
  -> Verification
  -> Audit + Memory + Result
  -> Realtime Event Bus (F29.16+)
```

## F29.0 — Architecture freeze

The F28 public API surface is the compatibility baseline.

Execution-domain boundaries:

- Planner proposes work and capabilities. It never owns credentials.
- Capability Resolver exposes provider-neutral capability/resource descriptors.
- Action Gateway remains the mandatory authorization boundary for provider side effects.
- Execution Resolver selects an eligible provider adapter after authorization.
- Provider adapters own provider-specific HTTP behavior, payload normalization, and response translation.
- Runtime must not branch on provider names.
- Neon remains the authoritative system of record.
- Redis realtime streams and WebSocket/SSE delivery are reserved for F29.16 onward and must not become the source of truth.

## F29.1 — ExecutionRequest

`app.execution.contracts.ExecutionRequest` is the canonical provider-neutral request.

It carries tenant/worker/agent/job/run lineage, a structured capability, a structured resource, operation/input, correlation id, idempotency key, and execution preferences.

## F29.2 — ExecutionResult

`ExecutionResult` normalizes status, provider/adapter identity, operation, output, verification, artifacts, provider request id, timing, and optional normalized failure data.

## F29.3 — Execution errors

Providers translate transport/provider failures into the common codes:

- authentication_error
- authorization_error
- resource_not_found
- rate_limited
- temporary_provider_error
- validation_error
- policy_blocked
- approval_required
- verification_failed
- timeout
- provider_unavailable
- unsupported_operation

Internal provider responses are never used as the public safe message.

## F29.4 — CapabilityDescriptor v2

Capabilities are structured contracts containing:

- scope
- provider
- resource type
- provider operation
- mode (read/write/action)
- risk
- credential requirement
- side-effect flag
- approval recommendation
- input/output schemas
- description and target

## F29.5 — ResourceDescriptor

Resources use a provider-neutral representation with external identity, display metadata, health, available capability scopes, optional web URL, and provider configuration.

## F29.6 — Provider protocol

Every execution provider implements the same interface:

- discover_resources
- discover_capabilities
- check_health
- normalize_input
- execute
- verify

The first five providers are native API implementations.

## F29.7 — Provider registry

`ProviderRegistry` owns provider loading, manifests, kind/version metadata, enabled state, and capability lookup.

The default registry contains:

- github
- gmail
- slack
- google_drive
- google_calendar

Browser and MCP providers intentionally do not exist yet.

## F29.8 — Execution resolver

`ExecutionResolver` resolves by execution preference (native API first by default), exact capability support, adapter version, resource/provider match, resource health, credential availability, and live provider health.

It does not silently execute an unhealthy or credential-incomplete provider.

## F29.9 — Native adapter SDK

`app.execution.providers.native` provides shared HTTP transport/error translation and a base provider contract.

Provider-specific HTTP details stay inside provider modules.

## F29.10 — Existing integration migration

GitHub, Gmail, Slack, Google Drive, and Google Calendar now implement the universal provider protocol.

The public integration/capability APIs consume the same registry and manifests used by execution.

The pre-F29 `app.integrations.builtin` module remains only as a compatibility facade over the universal providers; it contains no independent provider implementation.

The Action Gateway can invoke `UniversalProviderExecutor`, which loads a tenant-bound integration resource and credential, resolves the provider generically, executes it, and maps the result to the legacy gateway result shape.

## Security invariants

- Provider execution does not replace Action Gateway authorization.
- Capabilities do not grant themselves.
- Provider credentials remain behind the encrypted credential storage boundary.
- Cross-tenant resource resolution is denied.
- Side effects retain idempotency requirements.
- Native adapter availability does not activate autonomous Runtime execution.
- Browser and MCP fallback are not enabled in F29.0–F29.10.

## Realtime responsibility reservation

F29.16+ will use the existing Upstash Redis account as the event/fan-out backbone, with Neon remaining authoritative. Important events will be persisted after database commit and then delivered through Redis Streams; FastAPI WebSocket/SSE will be client transport only.
