"""Retain structured failure scope for preparation and execution model attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_preparation_retry"
down_revision = "0013_ai_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("planner_attempts", sa.Column("failure_details", JSONB,
        nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade():
    op.drop_column("planner_attempts", "failure_details")
