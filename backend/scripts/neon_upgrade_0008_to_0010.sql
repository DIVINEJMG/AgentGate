-- Run the entire script together in Neon SQL Editor on the existing AgentGate database.
-- Requires a SQL Editor login allowed to SET ROLE audoryn_migrator.
-- Source: Alembic migrations 0009 and 0010. No credentials are included.
-- Stop local FastAPI and keep new integration feature flags disabled during this step.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '90s';
SET LOCAL search_path = public;
SET LOCAL ROLE audoryn_migrator;

-- Lock and verify the migration ledger before changing the schema.
LOCK TABLE public.alembic_version IN EXCLUSIVE MODE;
DO $migration_guard$
BEGIN
    IF (SELECT count(*) FROM public.alembic_version) <> 1
       OR (SELECT max(version_num) FROM public.alembic_version) IS DISTINCT FROM '0008_f31_autonomy'
    THEN
        RAISE EXCEPTION 'Expected migration 0008_f31_autonomy. Stop and check the current revision before applying this script.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audoryn_app') THEN
        RAISE EXCEPTION 'Expected runtime role audoryn_app is missing.';
    END IF;
END
$migration_guard$;

-- Running upgrade 0008_f31_autonomy -> 0009_integration_foundation

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
        );

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
        );

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
        );

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
        );

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
        );

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
        );

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
        );

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
        );

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
        WHERE family = 'integration.execute';;

UPDATE alembic_version SET version_num='0009_integration_foundation' WHERE alembic_version.version_num = '0008_f31_autonomy';

-- Running upgrade 0009_integration_foundation -> 0010_github_coding

CREATE TABLE github_onboarding (
    owner_id UUID NOT NULL,
    reconnect_connection_id UUID,
    state_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    credential_ciphertext TEXT,
    status VARCHAR(32) NOT NULL,
    organization_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(owner_id) REFERENCES human_identities (id),
    FOREIGN KEY(reconnect_connection_id) REFERENCES integrations (id) ON DELETE CASCADE,
    UNIQUE (state_hash),
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE TABLE github_installation_bindings (
    connection_id UUID NOT NULL,
    installation_id VARCHAR(64) NOT NULL,
    account_id VARCHAR(64) NOT NULL,
    verified_user_id VARCHAR(64) NOT NULL,
    permissions JSONB NOT NULL,
    status VARCHAR(32) NOT NULL,
    organization_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (connection_id),
    FOREIGN KEY(connection_id) REFERENCES integrations (id) ON DELETE CASCADE,
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE TABLE integration_resource_aliases (
    resource_id UUID NOT NULL,
    alias VARCHAR(512) NOT NULL,
    organization_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_resource_alias UNIQUE (resource_id, alias),
    FOREIGN KEY(resource_id) REFERENCES integration_resources (id) ON DELETE CASCADE,
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE TABLE github_action_evidence (
    connection_id UUID NOT NULL,
    work_item_id UUID NOT NULL,
    action_key VARCHAR(255) NOT NULL,
    operation VARCHAR(128) NOT NULL,
    resource_external_id VARCHAR(512) NOT NULL,
    payload_fingerprint VARCHAR(64) NOT NULL,
    external_id VARCHAR(255),
    evidence JSONB NOT NULL,
    organization_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_github_action_evidence UNIQUE (connection_id, action_key),
    FOREIGN KEY(connection_id) REFERENCES integrations (id) ON DELETE CASCADE,
    FOREIGN KEY(work_item_id) REFERENCES work_items (id) ON DELETE CASCADE,
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE TABLE coding_sessions (
    work_item_id UUID NOT NULL,
    resource_id UUID NOT NULL,
    sandbox_id VARCHAR(255),
    base_sha VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    fencing_token BIGINT NOT NULL,
    active_seconds INTEGER NOT NULL,
    active_since TIMESTAMP WITH TIME ZONE,
    last_activity_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    evidence JSONB NOT NULL,
    organization_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_coding_workspace UNIQUE (work_item_id, resource_id),
    FOREIGN KEY(work_item_id) REFERENCES work_items (id) ON DELETE CASCADE,
    FOREIGN KEY(resource_id) REFERENCES integration_resources (id) ON DELETE CASCADE,
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE TABLE coding_commands (
    session_id UUID NOT NULL,
    action_key VARCHAR(255) NOT NULL,
    command TEXT NOT NULL,
    process_id INTEGER,
    status VARCHAR(32) NOT NULL,
    output JSONB NOT NULL,
    organization_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_coding_command_action UNIQUE (session_id, action_key),
    FOREIGN KEY(session_id) REFERENCES coding_sessions (id) ON DELETE CASCADE,
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE TABLE coding_budgets (
    day VARCHAR(10) NOT NULL,
    reserved_seconds INTEGER NOT NULL,
    organization_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_coding_daily_budget UNIQUE (organization_id, day),
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_github_installation ON github_installation_bindings (installation_id);

ALTER TABLE integration_task_grants ADD COLUMN constraints JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE outbox_events ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0;

ALTER TABLE outbox_events ADD COLUMN next_delivery_at TIMESTAMPTZ;

CREATE INDEX ix_outbox_delayed ON outbox_events (next_delivery_at, created_at) WHERE published_at IS NULL;

UPDATE alembic_version SET version_num='0010_github_coding' WHERE alembic_version.version_num = '0009_integration_foundation';

-- Give the existing runtime role data access to only the new application tables.
-- This grants no schema creation, table alteration, role membership or ownership.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    public.integration_connection_states,
    public.integration_resources,
    public.integration_access_grants,
    public.integration_task_grants,
    public.integration_event_inbox,
    public.integration_notifications,
    public.integration_auth_states,
    public.integration_event_subscriptions,
    public.github_onboarding,
    public.github_installation_bindings,
    public.integration_resource_aliases,
    public.github_action_evidence,
    public.coding_sessions,
    public.coding_commands,
    public.coding_budgets TO audoryn_app;

COMMIT;

-- Expected result: 0010_github_coding.
SELECT version_num FROM public.alembic_version;
