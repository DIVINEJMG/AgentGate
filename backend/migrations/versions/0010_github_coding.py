"""GitHub App and isolated coding state. No legacy grants or ciphertext changed."""

from alembic import op

revision = "0010_github_coding"
down_revision = "0009_integration_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE github_onboarding (\n\towner_id UUID NOT NULL, \n\treconnect_connection_id UUID, \n\tstate_hash VARCHAR(64) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcredential_ciphertext TEXT, \n\tstatus VARCHAR(32) NOT NULL, \n\torganization_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id) REFERENCES human_identities (id), \n\tFOREIGN KEY(reconnect_connection_id) REFERENCES integrations (id) ON DELETE CASCADE, \n\tUNIQUE (state_hash), \n\tFOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE github_installation_bindings (\n\tconnection_id UUID NOT NULL, \n\tinstallation_id VARCHAR(64) NOT NULL, \n\taccount_id VARCHAR(64) NOT NULL, \n\tverified_user_id VARCHAR(64) NOT NULL, \n\tpermissions JSONB NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\torganization_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (connection_id), \n\tFOREIGN KEY(connection_id) REFERENCES integrations (id) ON DELETE CASCADE, \n\tFOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE integration_resource_aliases (\n\tresource_id UUID NOT NULL, \n\talias VARCHAR(512) NOT NULL, \n\torganization_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_resource_alias UNIQUE (resource_id, alias), \n\tFOREIGN KEY(resource_id) REFERENCES integration_resources (id) ON DELETE CASCADE, \n\tFOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE github_action_evidence (\n\tconnection_id UUID NOT NULL, \n\twork_item_id UUID NOT NULL, \n\taction_key VARCHAR(255) NOT NULL, \n\toperation VARCHAR(128) NOT NULL, \n\tresource_external_id VARCHAR(512) NOT NULL, \n\tpayload_fingerprint VARCHAR(64) NOT NULL, \n\texternal_id VARCHAR(255), \n\tevidence JSONB NOT NULL, \n\torganization_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_github_action_evidence UNIQUE (connection_id, action_key), \n\tFOREIGN KEY(connection_id) REFERENCES integrations (id) ON DELETE CASCADE, \n\tFOREIGN KEY(work_item_id) REFERENCES work_items (id) ON DELETE CASCADE, \n\tFOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE coding_sessions (\n\twork_item_id UUID NOT NULL, \n\tresource_id UUID NOT NULL, \n\tsandbox_id VARCHAR(255), \n\tbase_sha VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\tfencing_token BIGINT NOT NULL, \n\tactive_seconds INTEGER NOT NULL, \n\tactive_since TIMESTAMP WITH TIME ZONE, \n\tlast_activity_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tevidence JSONB NOT NULL, \n\torganization_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_coding_workspace UNIQUE (work_item_id, resource_id), \n\tFOREIGN KEY(work_item_id) REFERENCES work_items (id) ON DELETE CASCADE, \n\tFOREIGN KEY(resource_id) REFERENCES integration_resources (id) ON DELETE CASCADE, \n\tFOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE coding_commands (\n\tsession_id UUID NOT NULL, \n\taction_key VARCHAR(255) NOT NULL, \n\tcommand TEXT NOT NULL, \n\tprocess_id INTEGER, \n\tstatus VARCHAR(32) NOT NULL, \n\toutput JSONB NOT NULL, \n\torganization_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_coding_command_action UNIQUE (session_id, action_key), \n\tFOREIGN KEY(session_id) REFERENCES coding_sessions (id) ON DELETE CASCADE, \n\tFOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE coding_budgets (\n\tday VARCHAR(10) NOT NULL, \n\treserved_seconds INTEGER NOT NULL, \n\torganization_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_coding_daily_budget UNIQUE (organization_id, day), \n\tFOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE INDEX ix_github_installation ON github_installation_bindings (installation_id)"
    )
    op.execute(
        "ALTER TABLE integration_task_grants ADD COLUMN constraints JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute("ALTER TABLE outbox_events ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE outbox_events ADD COLUMN next_delivery_at TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX ix_outbox_delayed ON outbox_events (next_delivery_at, created_at) WHERE published_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_outbox_delayed")
    op.execute("ALTER TABLE outbox_events DROP COLUMN next_delivery_at")
    op.execute("ALTER TABLE outbox_events DROP COLUMN delivery_attempts")
    op.execute("ALTER TABLE integration_task_grants DROP COLUMN constraints")
    op.drop_table("coding_budgets")
    op.drop_table("coding_commands")
    op.drop_table("coding_sessions")
    op.drop_table("github_action_evidence")
    op.drop_table("integration_resource_aliases")
    op.drop_table("github_installation_bindings")
    op.drop_table("github_onboarding")
