-- Generated from backend/migrations/versions/0011_smart_planner.py.
-- Run in Neon SQL Editor with a role allowed to SET ROLE audoryn_migrator.
-- This does not resume jobs or change credentials.
BEGIN;
SET LOCAL ROLE audoryn_migrator;
SET LOCAL search_path = public;
DO $$
BEGIN
    IF (SELECT count(*) FROM alembic_version) <> 1
       OR NOT EXISTS (SELECT 1 FROM alembic_version WHERE version_num = '0010_github_coding') THEN
        RAISE EXCEPTION 'Expected migration 0010_github_coding. No changes applied.';
    END IF;
    IF to_regclass('public.planner_decisions') IS NOT NULL
       OR to_regclass('public.planner_attempts') IS NOT NULL THEN
        RAISE EXCEPTION 'Planner tables already exist. Review migration state before proceeding.';
    END IF;
END $$;

CREATE TABLE planner_decisions (
    id UUID NOT NULL,
    organization_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    work_item_id UUID NOT NULL,
    run_id UUID NOT NULL,
    step_index INTEGER NOT NULL,
    context_fingerprint VARCHAR(64) NOT NULL,
    routes JSONB NOT NULL,
    deadline TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(32) NOT NULL,
    generation INTEGER NOT NULL,
    recovery_round INTEGER NOT NULL,
    retry_at TIMESTAMP WITH TIME ZONE,
    accepted_provider VARCHAR(64),
    accepted_model VARCHAR(255),
    accepted_proposal JSONB,
    PRIMARY KEY (id),
    CONSTRAINT uq_planner_pending_decision UNIQUE (work_item_id, run_id, step_index),
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
    FOREIGN KEY(work_item_id) REFERENCES work_items (id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE
);

CREATE TABLE planner_attempts (
    id UUID NOT NULL,
    organization_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    decision_id UUID NOT NULL,
    route_index INTEGER NOT NULL,
    recovery_round INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    provider VARCHAR(64) NOT NULL,
    model VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    call_count INTEGER NOT NULL,
    transport_success BOOLEAN NOT NULL,
    schema_valid BOOLEAN NOT NULL,
    accepted BOOLEAN NOT NULL,
    latency_ms INTEGER,
    error_category VARCHAR(64),
    retryable BOOLEAN NOT NULL,
    retry_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_planner_route_attempt UNIQUE (decision_id, recovery_round, route_index),
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
    FOREIGN KEY(decision_id) REFERENCES planner_decisions (id) ON DELETE CASCADE
);


GRANT SELECT, INSERT, UPDATE, DELETE ON public.planner_decisions, public.planner_attempts TO audoryn_app;
UPDATE public.alembic_version SET version_num = '0011_smart_planner'
WHERE version_num = '0010_github_coding';
COMMIT;

SELECT version_num FROM public.alembic_version;
SELECT to_regclass('public.planner_decisions') AS decisions,
       to_regclass('public.planner_attempts') AS attempts;
