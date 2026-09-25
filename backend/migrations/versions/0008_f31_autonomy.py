"""F31.21-F31.30 memory and artifact analysis metadata."""

from alembic import op

revision = "0008_f31_autonomy"
down_revision = "0007_f31_conversation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE memories
        ADD COLUMN IF NOT EXISTS memory_type VARCHAR(32) NOT NULL DEFAULT 'operational',
        ADD COLUMN IF NOT EXISTS source VARCHAR(64) NOT NULL DEFAULT 'human',
        ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS sensitivity VARCHAR(24) NOT NULL DEFAULT 'internal',
        ADD COLUMN IF NOT EXISTS status VARCHAR(24) NOT NULL DEFAULT 'active'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_memories_org_owner_type
        ON memories (organization_id, owner_id, memory_type)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS artifact_analyses (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            artifact_id UUID NOT NULL UNIQUE REFERENCES artifacts(id) ON DELETE CASCADE,
            analyzer_role VARCHAR(32) NOT NULL,
            provider VARCHAR(64),
            model VARCHAR(255),
            status VARCHAR(32) NOT NULL,
            findings JSONB NOT NULL DEFAULT '{}'::jsonb,
            provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
            sensitivity VARCHAR(24) NOT NULL DEFAULT 'internal',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_artifact_analyses_org_created
        ON artifact_analyses (organization_id, created_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS artifact_analyses")
    op.execute("DROP INDEX IF EXISTS ix_memories_org_owner_type")
    op.execute(
        """
        ALTER TABLE memories
        DROP COLUMN IF EXISTS status,
        DROP COLUMN IF EXISTS sensitivity,
        DROP COLUMN IF EXISTS provenance,
        DROP COLUMN IF EXISTS source,
        DROP COLUMN IF EXISTS memory_type
        """
    )
