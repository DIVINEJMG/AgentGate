"""Add F27 provider-cutover metadata safely on fresh and upgraded databases."""

from alembic import op

revision = "0003_f27_provider_metadata"
down_revision = "0002_legacy_migration_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE work_items "
        "ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(180)"
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'uq_work_item_idempotency'
          ) THEN
            ALTER TABLE work_items
            ADD CONSTRAINT uq_work_item_idempotency
            UNIQUE (organization_id, idempotency_key);
          END IF;
        END
        $$;
        """
    )
    op.execute(
        "ALTER TABLE artifacts "
        "ADD COLUMN IF NOT EXISTS size_bytes BIGINT"
    )
    op.execute(
        "ALTER TABLE artifacts "
        "ADD COLUMN IF NOT EXISTS checksum_sha256 VARCHAR(64)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE work_items "
        "DROP CONSTRAINT IF EXISTS uq_work_item_idempotency"
    )
    op.execute(
        "ALTER TABLE artifacts DROP COLUMN IF EXISTS checksum_sha256"
    )
    op.execute(
        "ALTER TABLE artifacts DROP COLUMN IF EXISTS size_bytes"
    )
    op.execute(
        "ALTER TABLE work_items DROP COLUMN IF EXISTS idempotency_key"
    )
