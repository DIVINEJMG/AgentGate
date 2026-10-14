"""First-run setup of new workspaces: welcome answers, starter setup and getting-started checklist."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0018_organization_onboarding"
down_revision = "0017_run_plan_steps"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organization_onboarding",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("human_identities.id"), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="welcome"),
        sa.Column("answers", JSONB(), nullable=False, server_default="{}"),
        sa.Column("setup", JSONB(), nullable=False, server_default="{}"),
        sa.Column("checklist_dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("organization_onboarding")
