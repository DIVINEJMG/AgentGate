"""Add local human authentication credentials."""

from alembic import op

revision = "0005_local_auth_credentials"
down_revision = "0004_f27_queue_failures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS local_auth_credentials (
            user_id UUID PRIMARY KEY REFERENCES human_identities(id) ON DELETE CASCADE,
            password_hash VARCHAR(512) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS local_auth_credentials")
