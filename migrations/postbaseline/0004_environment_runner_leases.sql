-- Durable runner lease and heartbeat metadata.
-- A lease is diagnostic/recovery metadata; expiry never triggers automatic redispatch.
ALTER TABLE amodex_environment_runner_jobs
    ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS heartbeat_count BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_amodex_environment_runner_jobs_lease
    ON amodex_environment_runner_jobs(state, lease_expires_at)
    WHERE state IN ('CLAIMED','RUNNING');
