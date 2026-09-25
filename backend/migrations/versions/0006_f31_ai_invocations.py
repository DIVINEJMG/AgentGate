"""Persist safe F31 AI invocation telemetry."""

from alembic import op

revision = "0006_f31_ai_invocations"
down_revision = "0005_local_auth_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_invocations (
            id UUID PRIMARY KEY,
            organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
            worker_id UUID REFERENCES workers(id) ON DELETE SET NULL,
            job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
            run_id UUID REFERENCES runs(id) ON DELETE SET NULL,
            thread_id UUID,
            role VARCHAR(32) NOT NULL,
            provider VARCHAR(64) NOT NULL,
            model VARCHAR(255) NOT NULL,
            latency_ms INTEGER,
            success BOOLEAN NOT NULL DEFAULT FALSE,
            request_id VARCHAR(255),
            usage JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_category VARCHAR(64),
            schema_name VARCHAR(128),
            correlation_id VARCHAR(128),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ai_invocations_org_created
        ON ai_invocations (organization_id, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ai_invocations_role_created
        ON ai_invocations (role, created_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_invocations")
