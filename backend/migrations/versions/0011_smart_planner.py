"""Durable planner decisions and attempts; no legacy state is rewritten."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011_smart_planner"
down_revision = "0010_github_coding"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "planner_decisions",
        *common(),
        sa.Column(
            "work_item_id", UUID, sa.ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("run_id", UUID, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("context_fingerprint", sa.String(64), nullable=False),
        sa.Column("routes", JSONB, nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("recovery_round", sa.Integer, nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_provider", sa.String(64)),
        sa.Column("accepted_model", sa.String(255)),
        sa.Column("accepted_proposal", JSONB),
        sa.UniqueConstraint(
            "work_item_id", "run_id", "step_index", name="uq_planner_pending_decision"
        ),
    )
    op.create_table(
        "planner_attempts",
        *common(),
        sa.Column(
            "decision_id",
            UUID,
            sa.ForeignKey("planner_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("route_index", sa.Integer, nullable=False),
        sa.Column("recovery_round", sa.Integer, nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("call_count", sa.Integer, nullable=False),
        sa.Column("transport_success", sa.Boolean, nullable=False),
        sa.Column("schema_valid", sa.Boolean, nullable=False),
        sa.Column("accepted", sa.Boolean, nullable=False),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("error_category", sa.String(64)),
        sa.Column("retryable", sa.Boolean, nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "decision_id", "recovery_round", "route_index", name="uq_planner_route_attempt"
        ),
    )


def downgrade():
    op.drop_table("planner_attempts")
    op.drop_table("planner_decisions")
