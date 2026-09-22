"""Persist QStash delivery failures for Needs Attention."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_f27_queue_failures"
down_revision = "0003_f27_provider_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "queue_delivery_failures",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_message_id", sa.String(length=180), nullable=False),
        sa.Column("dlq_id", sa.String(length=180), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("retried", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("destination_url", sa.String(length=1024), nullable=False),
        sa.Column("failure_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_message_id"),
    )
    op.create_index(
        "ix_queue_delivery_failures_org_created",
        "queue_delivery_failures",
        ["organization_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_queue_delivery_failures_org_created", table_name="queue_delivery_failures")
    op.drop_table("queue_delivery_failures")
