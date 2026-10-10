-- Matches backend/migrations/versions/0012_ai_workloads.py.
-- Use the same Neon database and administrative role used for migration 0011.
BEGIN;
SET LOCAL ROLE audoryn_migrator;
SET LOCAL search_path = public;
LOCK TABLE public.alembic_version IN EXCLUSIVE MODE;

DO $$
BEGIN
    IF (SELECT count(*) FROM public.alembic_version) <> 1
       OR NOT EXISTS (
           SELECT 1 FROM public.alembic_version
           WHERE version_num = '0011_smart_planner'
       ) THEN
        RAISE EXCEPTION 'Expected 0011_smart_planner. No changes applied.';
    END IF;
END $$;

ALTER TABLE public.planner_decisions
    ALTER COLUMN work_item_id DROP NOT NULL,
    ALTER COLUMN run_id DROP NOT NULL,
    ADD COLUMN command_id UUID,
    ADD COLUMN preparation_revision_id UUID,
    ADD COLUMN workload VARCHAR(32) NOT NULL DEFAULT 'complex',
    ADD COLUMN purpose VARCHAR(64) NOT NULL DEFAULT 'execution_planning',
    ADD CONSTRAINT fk_planner_preparation_command
        FOREIGN KEY (command_id) REFERENCES public.conversation_commands(id)
        ON DELETE CASCADE,
    ADD CONSTRAINT fk_planner_preparation_revision
        FOREIGN KEY (preparation_revision_id) REFERENCES public.job_revisions(id)
        ON DELETE CASCADE,
    ADD CONSTRAINT uq_preparation_decision
        UNIQUE (organization_id, command_id, purpose, step_index),
    ADD CONSTRAINT uq_worker_preparation_decision
        UNIQUE (organization_id, preparation_revision_id, purpose, step_index),
    ADD CONSTRAINT ck_planner_decision_target CHECK (
        (command_id IS NULL AND preparation_revision_id IS NULL
         AND work_item_id IS NOT NULL AND run_id IS NOT NULL)
        OR
        (command_id IS NOT NULL AND preparation_revision_id IS NULL
         AND work_item_id IS NULL AND run_id IS NULL)
        OR
        (preparation_revision_id IS NOT NULL AND command_id IS NULL
         AND work_item_id IS NULL AND run_id IS NULL)
    );

UPDATE public.alembic_version
SET version_num = '0012_ai_workloads'
WHERE version_num = '0011_smart_planner';

COMMIT;

SELECT version_num FROM public.alembic_version;
