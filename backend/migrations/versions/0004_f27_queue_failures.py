"""Persist QStash delivery failures for Needs Attention."""

from alembic import op

revision = "0004_f27_queue_failures"
down_revision = "0003_f27_provider_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fresh installs may already contain this table via 0001 current metadata.
    # Upgraded databases reach it here. Keep the revision safe for both paths.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_delivery_failures (
            organization_id UUID NOT NULL
                REFERENCES organizations(id) ON DELETE CASCADE,
            id UUID NOT NULL PRIMARY KEY,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            source_message_id VARCHAR(180) NOT NULL UNIQUE,
            dlq_id VARCHAR(180),
            status_code INTEGER,
            retried INTEGER NOT NULL,
            max_retries INTEGER NOT NULL,
            destination_url VARCHAR(1024) NOT NULL,
            failure_payload JSONB NOT NULL,
            resolved_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_queue_delivery_failures_org_created
        ON queue_delivery_failures (organization_id, created_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_queue_delivery_failures_org_created")
    op.execute("DROP TABLE IF EXISTS queue_delivery_failures")
