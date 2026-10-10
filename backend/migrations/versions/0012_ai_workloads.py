"""Allow durable complex preparation alongside existing execution decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0012_ai_workloads"
down_revision = "0011_smart_planner"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("planner_decisions", "work_item_id", nullable=True)
    op.alter_column("planner_decisions", "run_id", nullable=True)
    op.add_column("planner_decisions", sa.Column("command_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_planner_preparation_command",
        "planner_decisions",
        "conversation_commands",
        ["command_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column("planner_decisions", sa.Column("preparation_revision_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_planner_preparation_revision",
        "planner_decisions",
        "job_revisions",
        ["preparation_revision_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "planner_decisions",
        sa.Column("workload", sa.String(32), nullable=False, server_default="complex"),
    )
    op.add_column(
        "planner_decisions",
        sa.Column("purpose", sa.String(64), nullable=False, server_default="execution_planning"),
    )
    op.create_unique_constraint(
        "uq_preparation_decision",
        "planner_decisions",
        ["organization_id", "command_id", "purpose", "step_index"],
    )
    op.create_unique_constraint(
        "uq_worker_preparation_decision",
        "planner_decisions",
        ["organization_id", "preparation_revision_id", "purpose", "step_index"],
    )
    op.create_check_constraint(
        "ck_planner_decision_target",
        "planner_decisions",
        "(command_id IS NULL AND preparation_revision_id IS NULL AND work_item_id IS NOT NULL AND run_id IS NOT NULL) OR "
        "(command_id IS NOT NULL AND preparation_revision_id IS NULL AND work_item_id IS NULL AND run_id IS NULL) OR "
        "(preparation_revision_id IS NOT NULL AND command_id IS NULL AND work_item_id IS NULL AND run_id IS NULL)",
    )


def downgrade():
    # Downgrade is unavailable while preparation audit records exist; never delete them silently.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM planner_decisions WHERE command_id IS NOT NULL OR preparation_revision_id IS NOT NULL) "
        "THEN RAISE EXCEPTION 'Archive preparation decisions before downgrade'; END IF; END $$"
    )
    op.drop_constraint("ck_planner_decision_target", "planner_decisions", type_="check")
    op.drop_constraint("uq_preparation_decision", "planner_decisions", type_="unique")
    op.drop_constraint("uq_worker_preparation_decision", "planner_decisions", type_="unique")
    op.drop_constraint("fk_planner_preparation_command", "planner_decisions", type_="foreignkey")
    op.drop_constraint("fk_planner_preparation_revision", "planner_decisions", type_="foreignkey")
    for name in ("command_id", "preparation_revision_id", "workload", "purpose"):
        op.drop_column("planner_decisions", name)
    op.alter_column("planner_decisions", "work_item_id", nullable=False)
    op.alter_column("planner_decisions", "run_id", nullable=False)
