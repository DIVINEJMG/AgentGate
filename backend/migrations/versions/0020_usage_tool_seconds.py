"""Seconds spent in browser actions and running coding sandboxes, on each usage record
(commercial plan, phase 1)."""

import sqlalchemy as sa
from alembic import op

revision = "0020_usage_tool_seconds"
down_revision = "0019_usage_prompt_chars"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("usage_records", sa.Column("browser_seconds", sa.Float(), nullable=False, server_default="0"))
    op.add_column("usage_records", sa.Column("coding_seconds", sa.Float(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("usage_records", "coding_seconds")
    op.drop_column("usage_records", "browser_seconds")
