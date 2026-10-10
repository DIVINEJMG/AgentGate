-- Run as the table-owning migration role, after 0012_ai_workloads.
-- No credentials or inference prompts are stored in these columns.
BEGIN;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM alembic_version WHERE version_num IN ('0012_ai_workloads', '0013_ai_connections')) THEN
    RAISE EXCEPTION 'Apply migration 0012_ai_workloads first; no changes were applied.';
  END IF;
END $$;
ALTER TABLE ai_invocations ADD COLUMN IF NOT EXISTS transport_identity jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE planner_attempts ADD COLUMN IF NOT EXISTS transport_identity jsonb NOT NULL DEFAULT '{}'::jsonb;
UPDATE alembic_version SET version_num = '0013_ai_connections' WHERE version_num = '0012_ai_workloads';
COMMIT;
SELECT version_num FROM alembic_version;
