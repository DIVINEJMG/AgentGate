"""Persist the exact text endpoint and host selected for each model attempt."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_ai_connections"
down_revision = "0012_ai_workloads"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("ai_invocations", "planner_attempts"):
        op.add_column(
            table,
            sa.Column(
                "transport_identity", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
            ),
        )


def downgrade():
    for table in ("planner_attempts", "ai_invocations"):
        op.drop_column(table, "transport_identity")
