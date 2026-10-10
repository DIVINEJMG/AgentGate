"""Shared integration foundation. Additive; no credential ciphertext changes."""
from alembic import op

revision = "0009_integration_foundation"
down_revision = "0008_f31_autonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE integration_connection_states (
            connection_id UUID NOT NULL,
            owner_id UUID,
            ownership_state VARCHAR(32) NOT NULL,
            authorization_state VARCHAR(32) NOT NULL,
            reason TEXT NOT NULL,
            authority_version INTEGER NOT NULL,
            credential_metadata JSONB NOT NULL,
            account_id VARCHAR(255),
            organization_id UUID NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (connection_id),
            FOREIGN KEY(connection_id) REFERENCES integrations (id) ON DELETE CASCADE,
            FOREIGN KEY(owner_id) REFERENCES human_identities (id),
            FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE integration_resources (
            connection_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            external_id VARCHAR(512) NOT NULL,
            resource_type VARCHAR(64) NOT NULL,
            display_name VARCHAR(240) NOT NULL,
            capabilities JSONB NOT NULL,
            health VARCHAR(32) NOT NULL,
            configuration JSONB NOT NULL,
            web_url TEXT,
            organization_id UUID NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_integration_resource_identity UNIQUE (connection_id, external_id, resource_type),
            FOREIGN KEY(connection_id) REFERENCES integrations (id) ON DELETE CASCADE,
            FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE integration_access_grants (
            connection_id UUID NOT NULL,
            subject_type VARCHAR(16) NOT NULL,
            subject_id UUID NOT NULL,
            granted_by UUID NOT NULL,
            active BOOLEAN NOT NULL,
            organization_id UUID NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_integration_access_subject UNIQUE (connection_id, subject_type, subject_id),
            CONSTRAINT ck_integration_access_subject CHECK (subject_type in ('user','worker')),
            FOREIGN KEY(connection_id) REFERENCES integrations (id) ON DELETE CASCADE,
            FOREIGN KEY(granted_by) REFERENCES human_identities (id),
            FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE integration_task_grants (
            job_revision_id UUID NOT NULL,
            work_item_id UUID,
            worker_id UUID NOT NULL,
            initiating_user_id UUID NOT NULL,
            resource_id UUID NOT NULL,
            scopes JSONB NOT NULL,
            destinations JSONB NOT NULL,
            standing BOOLEAN NOT NULL,
            authority_version INTEGER NOT NULL,
            active BOOLEAN NOT NULL,
            organization_id UUID NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_integration_task_binding UNIQUE (job_revision_id, resource_id),
            FOREIGN KEY(job_revision_id) REFERENCES job_revisions (id) ON DELETE CASCADE,
            FOREIGN KEY(work_item_id) REFERENCES work_items (id) ON DELETE CASCADE,
            FOREIGN KEY(worker_id) REFERENCES workers (id) ON DELETE CASCADE,
            FOREIGN KEY(initiating_user_id) REFERENCES human_identities (id),
            FOREIGN KEY(resource_id) REFERENCES integration_resources (id) ON DELETE CASCADE,
            FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE integration_event_inbox (
            connection_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            delivery_id VARCHAR(255) NOT NULL,
            event_type VARCHAR(128) NOT NULL,
            resource_external_id VARCHAR(512) NOT NULL,
            evidence JSONB NOT NULL,
            status VARCHAR(32) NOT NULL,
            organization_id UUID NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_integration_event_delivery UNIQUE (provider, connection_id, delivery_id),
            FOREIGN KEY(connection_id) REFERENCES integrations (id) ON DELETE CASCADE,
            FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE integration_notifications (
            work_item_id UUID NOT NULL,
            notification_key VARCHAR(255) NOT NULL,
            message_id UUID NOT NULL,
            organization_id UUID NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_integration_notification UNIQUE (work_item_id, notification_key),
            FOREIGN KEY(work_item_id) REFERENCES work_items (id) ON DELETE CASCADE,
            FOREIGN KEY(message_id) REFERENCES conversation_messages (id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
            FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE integration_auth_states (
            state_hash VARCHAR(64) NOT NULL,
            owner_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            callback_url TEXT NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            consumed BOOLEAN NOT NULL,
            organization_id UUID NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (state_hash),
            FOREIGN KEY(owner_id) REFERENCES human_identities (id),
            FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE integration_event_subscriptions (
            job_revision_id UUID NOT NULL,
            resource_id UUID NOT NULL,
            event_type VARCHAR(128) NOT NULL,
            results_thread_id UUID NOT NULL,
            enabled BOOLEAN NOT NULL,
            organization_id UUID NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_integration_event_subscription UNIQUE (job_revision_id, resource_id, event_type),
            FOREIGN KEY(job_revision_id) REFERENCES job_revisions (id) ON DELETE CASCADE,
            FOREIGN KEY(resource_id) REFERENCES integration_resources (id) ON DELETE CASCADE,
            FOREIGN KEY(results_thread_id) REFERENCES conversation_threads (id) ON DELETE CASCADE,
            FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        INSERT INTO integration_connection_states
            (id, organization_id, connection_id, owner_id, ownership_state, authorization_state, reason, authority_version, credential_metadata, created_at, updated_at)
        SELECT i.id, i.organization_id, i.id, CASE WHEN m.id IS NULL THEN NULL ELSE h.id END,
            CASE WHEN m.id IS NULL THEN 'review_required' ELSE 'owned' END,
            CASE WHEN i.status = 'disconnected' THEN 'disconnected' ELSE 'connected' END,
            CASE WHEN m.id IS NULL THEN 'Recorded creator is missing or invalid; administrator review required.' ELSE '' END,
            1, '{}'::jsonb, now(), now()
        FROM integrations i
        LEFT JOIN human_identities h ON h.id::text = i.config->>'createdBy'
        LEFT JOIN organization_memberships m ON m.organization_id = i.organization_id AND m.user_id = h.id
        WHERE i.provider NOT IN ('browser', 'web_research');

        INSERT INTO integration_resources
            (id, organization_id, connection_id, provider, external_id, resource_type, display_name, capabilities, health, configuration, web_url, created_at, updated_at)
        SELECT id, organization_id, id, provider, COALESCE(config->>'resourceKey',id::text),
            COALESCE(config->>'resourceType','resource'),display_name,COALESCE(config->'availableCapabilities','[]'::jsonb),
            CASE WHEN status='connected' THEN 'healthy' ELSE 'unavailable' END,
            config, config->>'webUrl',now(),now()
        FROM integrations WHERE provider NOT IN ('browser','web_research');

        CREATE UNIQUE INDEX uq_integration_command_message ON conversation_commands (source_message_id)
        WHERE family = 'integration.execute';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_integration_command_message")
    op.drop_table('integration_event_subscriptions')
    op.drop_table('integration_auth_states')
    op.drop_table('integration_notifications')
    op.drop_table('integration_event_inbox')
    op.drop_table('integration_task_grants')
    op.drop_table('integration_access_grants')
    op.drop_table('integration_resources')
    op.drop_table('integration_connection_states')
