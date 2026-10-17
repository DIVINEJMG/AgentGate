"""Plan limits from the Console and usage per plan period (commercial plan, phase 2)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0021_entitlements"
down_revision = "0020_usage_tool_seconds"
branch_labels = None
depends_on = None

def _organization():
    return sa.ForeignKey("organizations.id", ondelete="CASCADE")


def _received():
    return sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade():
    op.create_table(
        "organization_entitlements",
        sa.Column("organization_id", UUID(as_uuid=True), _organization(), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("plan_code", sa.String(64)),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("entitlements", JSONB(), nullable=False, server_default="{}"),
        _received(),
    )
    op.create_table(
        "entitlement_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), _organization(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("document", JSONB(), nullable=False),
        _received(),
        sa.UniqueConstraint("organization_id", "version", name="uq_entitlement_snapshot_version"),
    )
    op.create_table(
        "entitlement_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("document", JSONB(), nullable=False),
        _received(),
    )
    op.create_table(
        "organization_usage",
        sa.Column("organization_id", UUID(as_uuid=True), _organization(), primary_key=True),
        sa.Column("period_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("browser_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("coding_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("web_research", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    for table in ("organization_usage", "entitlement_templates", "entitlement_snapshots", "organization_entitlements"):
        op.drop_table(table)
