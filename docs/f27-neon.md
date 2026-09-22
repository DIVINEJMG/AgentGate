# F27 Neon

Neon is the canonical PostgreSQL provider.

Staging project: audoryn-staging in AWS eu-central-1. The legacy Render PostgreSQL database remains preserved as a rollback source.

The staging migration path is guarded by LEGACY_DATABASE_URL and MIGRATION_EXECUTION_ENABLED; migration execution is disabled after use.

Production project: audoryn-production, separate from staging. Alembic is the only schema authority. CI renders the full fresh-install migration chain so later model additions cannot silently break new environments.
