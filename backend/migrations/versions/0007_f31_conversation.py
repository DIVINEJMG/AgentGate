"""F31.11 conversational command persistence."""

from alembic import op

revision = "0007_f31_conversation"
down_revision = "0006_f31_ai_invocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_threads (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            worker_id UUID REFERENCES workers(id) ON DELETE SET NULL,
            title VARCHAR(200) NOT NULL DEFAULT 'New conversation',
            status VARCHAR(24) NOT NULL DEFAULT 'active',
            created_by UUID NOT NULL REFERENCES human_identities(id),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_conversation_threads_org_updated
        ON conversation_threads (organization_id, updated_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_conversation_threads_worker_updated
        ON conversation_threads (worker_id, updated_at)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            thread_id UUID NOT NULL REFERENCES conversation_threads(id) ON DELETE CASCADE,
            role VARCHAR(24) NOT NULL,
            content TEXT NOT NULL,
            artifact_references JSONB NOT NULL DEFAULT '[]'::jsonb,
            command_references JSONB NOT NULL DEFAULT '[]'::jsonb,
            result_references JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT ck_conversation_message_role
                CHECK (role in ('human','worker','system'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_conversation_messages_thread_created
        ON conversation_messages (thread_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_conversation_messages_org_created
        ON conversation_messages (organization_id, created_at)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_commands (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            thread_id UUID NOT NULL REFERENCES conversation_threads(id) ON DELETE CASCADE,
            source_message_id UUID NOT NULL REFERENCES conversation_messages(id) ON DELETE CASCADE,
            family VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL,
            target_type VARCHAR(64),
            target_id VARCHAR(128),
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            receipt JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by UUID NOT NULL REFERENCES human_identities(id),
            requires_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_conversation_commands_org_created
        ON conversation_commands (organization_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_conversation_commands_thread_created
        ON conversation_commands (thread_id, created_at)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS worker_directives (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            worker_id UUID NOT NULL REFERENCES workers(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'active',
            created_by UUID NOT NULL REFERENCES human_identities(id),
            source_thread_id UUID REFERENCES conversation_threads(id) ON DELETE SET NULL,
            source_message_id UUID REFERENCES conversation_messages(id) ON DELETE SET NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_worker_directives_org_worker
        ON worker_directives (organization_id, worker_id, status)
        """
    )
    op.execute(
        """
        ALTER TABLE ai_invocations
        ADD CONSTRAINT fk_ai_invocations_thread
        FOREIGN KEY (thread_id) REFERENCES conversation_threads(id) ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai_invocations DROP CONSTRAINT IF EXISTS fk_ai_invocations_thread")
    op.execute("DROP TABLE IF EXISTS worker_directives")
    op.execute("DROP TABLE IF EXISTS conversation_commands")
    op.execute("DROP TABLE IF EXISTS conversation_messages")
    op.execute("DROP TABLE IF EXISTS conversation_threads")
