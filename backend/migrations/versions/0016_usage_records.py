"""Usage records: compute used by each runtime step and chat message (improvement plan, phase 0)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0016_usage_records"
down_revision = "0015_github_login"
branch_labels = None
depends_on = None

COUNTERS = ("db_statements", "db_rows", "qstash_messages", "redis_commands", "ai_calls",
            "ai_failed_calls", "ai_tokens_in", "ai_tokens_out", "duration_ms")


def upgrade():
    op.create_table(
        "usage_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_item_id", UUID(as_uuid=True), sa.ForeignKey("work_items.id", ondelete="CASCADE")),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("thread_id", UUID(as_uuid=True),
                  sa.ForeignKey("conversation_threads.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("step", sa.Integer()),
        sa.Column("phase", sa.String(32)),
        sa.Column("outcome", sa.String(32)),
        *[sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in COUNTERS],
        sa.Column("ai_models", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_records_work_item", "usage_records", ["work_item_id", "created_at"])
    op.create_index("ix_usage_records_org_created", "usage_records", ["organization_id", "created_at"])


def downgrade():
    op.drop_index("ix_usage_records_org_created", table_name="usage_records")
    op.drop_index("ix_usage_records_work_item", table_name="usage_records")
    op.drop_table("usage_records")
