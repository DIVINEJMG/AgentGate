# F26 legacy data migration

Aduoryn no longer reads AppDeploy directly. F26.30 accepts only user-approved legacy exports or
other explicitly approved source material.

Pipeline:

```text
approved source/export
        ↓
extract
        ↓
normalize
        ↓
validate
        ↓
PostgreSQL staging
        ↓
referential-integrity checks
        ↓
canonical tables
```

Every migrated identity keeps:

- `legacy_id`
- `new_id`
- `source_system`
- `migrated_at`

Migration 0002 creates:

- `migration_batches`
- `migration_staging_records`
- `migration_id_mappings`

Migration is repeatable and must fail before canonical writes when duplicate legacy identities,
unsupported entity types, or invalid required fields are found. Canonical promotion is intentionally
separate from extraction/staging so integrity can be inspected before cutover.

No production copy/paste migration and no direct AppDeploy access is part of this path.
