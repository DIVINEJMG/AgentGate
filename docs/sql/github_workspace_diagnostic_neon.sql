-- Read-only preflight. No source content, command output or credentials returned.
SELECT w.id AS work_item_id, w.status AS task_status,
       c.id AS coding_session_id, c.status AS workspace_status,
       (c.sandbox_id IS NOT NULL) AS sandbox_identity_recorded,
       c.base_sha, c.active_seconds, c.last_activity_at, c.expires_at,
       c.evidence->'initialization'->>'stage' AS initialization_stage,
       c.evidence->'initialization'->>'deadline' AS initialization_deadline,
       jsonb_array_length(COALESCE(c.evidence->'initialization'->'confirmedBundles', '[]'::jsonb)) AS confirmed_bundles,
       jsonb_array_length(COALESCE(c.evidence->'initialization'->'bundles', '[]'::jsonb)) AS total_bundles,
       (c.evidence ? 'baselineArtifactId') AS baseline_saved
FROM work_items w
LEFT JOIN coding_sessions c ON c.work_item_id = w.id AND c.organization_id = w.organization_id
WHERE w.organization_id = '11e0f443-e0c7-43bc-bbf1-74062069bd30'
  AND w.id = 'a232e578-73ea-407a-abcb-846b4a343e65';
