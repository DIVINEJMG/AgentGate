"""External GitHub login identity and browser-bound signup connector handoff."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0015_github_login"
down_revision = "0014_preparation_retry"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "external_auth_identities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("human_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_subject", sa.String(64), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_external_provider_subject"),
        sa.UniqueConstraint("user_id", "provider", name="uq_external_user_provider"),
    )
    op.create_table(
        "github_auth_flows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("state_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("browser_challenge", sa.String(64), nullable=False),
        sa.Column("failure_reason", sa.String(512)),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("human_identities.id", ondelete="CASCADE")
        ),
        sa.Column("credential_ciphertext", sa.Text()),
        sa.Column(
            "onboarding_id",
            UUID(as_uuid=True),
            sa.ForeignKey("github_onboarding.id", ondelete="SET NULL"),
        ),
        *timestamps(),
        sa.CheckConstraint("purpose in ('signin','signup','link')", name="ck_github_auth_purpose"),
    )
    op.create_index("ix_github_auth_expiry", "github_auth_flows", ["expires_at"])


def downgrade():
    op.drop_table("github_auth_flows")
    op.drop_table("external_auth_identities")
