# F27 Upstash Redis

Redis is coordination, never business truth.

Allowed uses include locks, leases, heartbeats, cache, rate limiting, pub/sub and short-lived idempotency acceleration. PostgreSQL remains authoritative.

Staging uses audoryn-redis-staging in eu-central-1 with TLS enabled and eviction disabled.

Production must use a separate audoryn-redis-production database. Staging credentials must never be reused for production.
