"""Usage resets given from the Console (commercial plan, phase 6): applied at once, or banked for
the workspace to use when it chooses. The plan and its period never change; each row keeps the
usage it cleared."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0023_usage_resets"
down_revision = "0022_user_content_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usage_resets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("grant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("note", sa.String(200)),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("used_by", UUID(as_uuid=True), sa.ForeignKey("human_identities.id", ondelete="SET NULL")),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("cleared", JSONB),
        sa.CheckConstraint("mode IN ('now','banked')", name="usage_reset_mode"),
        sa.UniqueConstraint("grant_id", "organization_id", name="uq_usage_reset_grant_organization"),
    )
    op.create_index("ix_usage_resets_available", "usage_resets", ["organization_id", "used_at"])


def downgrade():
    op.drop_index("ix_usage_resets_available", table_name="usage_resets")
    op.drop_table("usage_resets")
