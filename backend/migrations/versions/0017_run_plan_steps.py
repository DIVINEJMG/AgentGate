"""Saved run plans: the ordered steps every model follows (multi-step execution)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017_run_plan_steps"
down_revision = "0016_usage_records"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "run_plan_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope", sa.String(200), nullable=False, server_default=""),
        sa.Column("target", sa.String(500), nullable=False, server_default=""),
        sa.Column("depends_on", JSONB(), nullable=False, server_default="[]"),
        sa.Column("check", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("action_steps", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "position", name="uq_run_plan_step_position"),
    )
    op.create_index("ix_run_plan_steps_run", "run_plan_steps", ["run_id", "position"])


def downgrade():
    op.drop_index("ix_run_plan_steps_run", table_name="run_plan_steps")
    op.drop_table("run_plan_steps")
