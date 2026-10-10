-- Run as the table-owning migration role after 0013_ai_connections.
-- Preserves all saved tasks, decisions and attempts. Historical scope remains unknown.
BEGIN;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM alembic_version
    WHERE version_num IN ('0013_ai_connections', '0014_preparation_retry')) THEN
    RAISE EXCEPTION 'Apply 0013_ai_connections first; no changes applied.';
  END IF;
END $$;
ALTER TABLE planner_attempts
  ADD COLUMN IF NOT EXISTS failure_details jsonb NOT NULL DEFAULT '{}'::jsonb;
UPDATE alembic_version SET version_num = '0014_preparation_retry'
  WHERE version_num = '0013_ai_connections';
COMMIT;
SELECT version_num FROM alembic_version;

-- Optional read-only diagnostic: includes old and fresh preparation decisions.
SELECT c.id AS command_id, d.id AS decision_id, d.step_index AS preparation_sequence,
       d.status, d.deadline, a.model, a.status AS attempt_status,
       a.error_category, a.failure_details
FROM conversation_commands c
JOIN planner_decisions d ON d.command_id = c.id AND d.organization_id = c.organization_id
LEFT JOIN planner_attempts a ON a.decision_id = d.id AND a.organization_id = d.organization_id
WHERE c.id = '1d3f18ae-33cf-4bcc-bfc0-f79fd89378aa'::uuid
ORDER BY d.step_index DESC, a.created_at;
