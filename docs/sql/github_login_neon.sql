-- Apply only after 0014_preparation_retry. Does not change existing accounts or connections.
BEGIN;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM alembic_version WHERE version_num = '0014_preparation_retry') THEN
    RAISE EXCEPTION 'Expected migration 0014_preparation_retry; check current schema before applying';
  END IF;
END $$;
CREATE TABLE external_auth_identities (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES human_identities(id) ON DELETE CASCADE,
  provider VARCHAR(32) NOT NULL,
  provider_subject VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_external_provider_subject UNIQUE(provider, provider_subject),
  CONSTRAINT uq_external_user_provider UNIQUE(user_id, provider)
);
CREATE TABLE github_auth_flows (
  id UUID PRIMARY KEY,
  state_hash VARCHAR(64) NOT NULL UNIQUE,
  browser_challenge VARCHAR(64) NOT NULL,
  failure_reason VARCHAR(512),
  purpose VARCHAR(16) NOT NULL,
  status VARCHAR(32) NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  user_id UUID REFERENCES human_identities(id) ON DELETE CASCADE,
  credential_ciphertext TEXT,
  onboarding_id UUID REFERENCES github_onboarding(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT ck_github_auth_purpose CHECK (purpose IN ('signin','signup','link'))
);
CREATE INDEX ix_github_auth_expiry ON github_auth_flows(expires_at);
UPDATE alembic_version SET version_num = '0015_github_login'
WHERE version_num = '0014_preparation_retry';
COMMIT;
SELECT version_num FROM alembic_version;
