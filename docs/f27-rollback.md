# F27 Rollback

If Neon fails, keep runtime disabled, restore the legacy PostgreSQL connection and redeploy.

If Redis fails, restore the prior coordination provider and rebuild disposable coordination state from PostgreSQL.

If Blob fails, disable artifact writes; PostgreSQL metadata remains canonical.

If QStash fails, pause schedule authority. Existing PostgreSQL WorkItems remain canonical.

Legacy Render PostgreSQL and Redis stay quarantined through the rollback window. Do not delete them as part of F27.
