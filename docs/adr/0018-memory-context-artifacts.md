# ADR 0018 — Memory, Context & Artifacts

## Status
Accepted for Foundation 17.

## Context
Foundation 16 can execute durable Runs, but each Run starts without durable business context and result payloads live only inside Run/Step records. Persistent context must improve continuity without becoming an authorization source or an uncontrolled self-modifying memory system.

## Decision
- Memory has three scopes: automatic short-lived Run Memory, human-controlled Worker Memory, and human-managed Organization Knowledge.
- Managed Runtime may write Run Memory after successful execution. It cannot directly create long-term Worker or Organization memory.
- A human with `memory.manage` may create long-term memory, archive it, or explicitly promote active Run Memory into Worker Memory.
- Retention settings create `expiresAt` context-eligibility boundaries. Expired records are excluded from context assembly but retained for auditability in F17; physical deletion is not claimed.
- Context assembly reads one bounded tenant-scoped page, excludes archived/expired records, uses deterministic lexical relevance plus recency, and selects at most eight entries. F17 does not claim vector or semantic retrieval.
- Selected memory IDs are stored on the Run as a context manifest. Memories are labeled untrusted context in AI prompts and are never supplied to capability, risk, policy, approval or incident-control decisions.
- Successful Runs create a JSON result artifact in AppDeploy Storage with tenant-scoped metadata in the database. Human artifact access requires `artifacts.read` and uses short-lived signed URLs.
- Artifact/memory persistence is part of the completion path; it does not introduce direct provider execution.

## Consequences
Workers gain continuity and inspectable durable outputs while authority remains entirely in the existing Audoryn control plane. Long-term self-writing memory, embeddings/vector search, bulk retention deletion and cross-provider knowledge ingestion remain outside F17.

