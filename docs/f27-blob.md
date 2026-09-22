# F27 Upstash Blob

Artifact bytes live in private Upstash Blob buckets behind the ObjectStorage interface. PostgreSQL stores metadata and object references only.

Staging bucket: audoryn-artifacts-staging.

Tenant keys are prefixed by organization and traversal is rejected. CI covers tenant isolation and large-payload pass-through.

Production must use a distinct private audoryn-artifacts-production bucket and a server-only bucket token.
