"""Bring Agent Identity persistence to TypeScript parity."""

from alembic import op

revision = "0006_agent_identity_parity"
down_revision = "0005_local_auth_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE agent_identities
        ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''
        """
    )
    op.execute(
        """
        ALTER TABLE agent_credentials
        ADD COLUMN IF NOT EXISTS scopes JSONB NOT NULL
        DEFAULT '["agent.authenticate"]'::jsonb
        """
    )
    op.execute(
        """
        ALTER TABLE agent_credentials
        ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE
        """
    )
    op.execute(
        """
        ALTER TABLE agent_credentials
        ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP WITH TIME ZONE
        """
    )
    op.execute(
        """
        ALTER TABLE agent_credentials
        ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP WITH TIME ZONE
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_credential_version
        ON agent_credentials (agent_id, version)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_agent_credential_version")
    op.execute("ALTER TABLE agent_credentials DROP COLUMN IF EXISTS revoked_at")
    op.execute("ALTER TABLE agent_credentials DROP COLUMN IF EXISTS last_used_at")
    op.execute("ALTER TABLE agent_credentials DROP COLUMN IF EXISTS expires_at")
    op.execute("ALTER TABLE agent_credentials DROP COLUMN IF EXISTS scopes")
    op.execute("ALTER TABLE agent_identities DROP COLUMN IF EXISTS description")
