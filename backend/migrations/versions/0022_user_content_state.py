"""What each person has read or dismissed in the app: upgrade prompts now, news entries later
(plans, news and upgrade prompts plan)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0022_user_content_state"
down_revision = "0021_entitlements"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_content_state",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("human_identities.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("item_key", sa.String(200), primary_key=True),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("user_content_state")
