-- Legacy rows remain readable and cannot acquire a new dispatch authorization.
ALTER TABLE amodex_environment_runner_jobs
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL
        CHECK (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    ADD COLUMN IF NOT EXISTS intent_digest TEXT NULL CHECK (intent_digest ~ '^[0-9a-f]{64}$'),
    ADD COLUMN IF NOT EXISTS dispatch_attempt_id UUID NULL,
    ADD COLUMN IF NOT EXISTS dispatch_status TEXT NULL
        CHECK (dispatch_status IN ('PENDING','STARTED','CONFIRMED','UNKNOWN','NOT_REQUIRED')),
    ADD COLUMN IF NOT EXISTS dispatch_target JSONB NULL,
    ADD COLUMN IF NOT EXISTS dispatch_started_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS dispatch_finished_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS dispatch_error_code TEXT NULL;

-- NULL keys deliberately permit pre-R3 jobs. One key is one intention per Environment.
CREATE UNIQUE INDEX IF NOT EXISTS idx_runner_job_intent
    ON amodex_environment_runner_jobs(environment_id, idempotency_key);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='idx_runner_job_intent'
          AND conrelid='amodex_environment_runner_jobs'::regclass
    ) THEN
        ALTER TABLE amodex_environment_runner_jobs
            ADD CONSTRAINT idx_runner_job_intent UNIQUE USING INDEX idx_runner_job_intent;
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_runner_dispatch_attempt
    ON amodex_environment_runner_jobs(dispatch_attempt_id);
