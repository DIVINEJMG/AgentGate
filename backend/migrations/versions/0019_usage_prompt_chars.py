"""Characters sent to models per prompt section, on each usage record (context-quality plan)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019_usage_prompt_chars"
down_revision = "0018_organization_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("usage_records", sa.Column("prompt_chars", JSONB(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("usage_records", "prompt_chars")
