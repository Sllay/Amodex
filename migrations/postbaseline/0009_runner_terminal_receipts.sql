-- No historical attempt/receipt is fabricated. Claim mints execution identity.
ALTER TABLE amodex_environment_runner_jobs
    ADD COLUMN IF NOT EXISTS execution_attempt_id UUID NULL,
    ADD COLUMN IF NOT EXISTS terminal_receipt_id UUID NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_runner_execution_attempt
    ON amodex_environment_runner_jobs(execution_attempt_id);

CREATE TABLE IF NOT EXISTS amodex_runner_terminal_receipts (
    job_id UUID PRIMARY KEY REFERENCES amodex_environment_runner_jobs(job_id) ON DELETE CASCADE,
    receipt_id UUID NOT NULL UNIQUE,
    attempt_id UUID NOT NULL,
    github_run_id TEXT NOT NULL,
    report_digest TEXT NOT NULL CHECK (report_digest ~ '^[0-9a-f]{64}$'),
    audit_id UUID NOT NULL UNIQUE REFERENCES amodex_audit_events(audit_id),
    receipt JSONB NOT NULL,
    authorized_until TIMESTAMPTZ NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL,
    UNIQUE (job_id, receipt_id),
    UNIQUE (job_id, attempt_id)
);

CREATE OR REPLACE FUNCTION amodex_check_terminal_receipt_lease() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.authorized_until <= clock_timestamp() THEN
        RAISE EXCEPTION 'terminal lease expired' USING ERRCODE='23514',
            CONSTRAINT='runner_receipt_lease_valid';
    END IF;
    RETURN NEW;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='runner_receipt_lease_valid'
                   AND tgrelid='amodex_runner_terminal_receipts'::regclass) THEN
        CREATE CONSTRAINT TRIGGER runner_receipt_lease_valid
            AFTER INSERT ON amodex_runner_terminal_receipts
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION amodex_check_terminal_receipt_lease();
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='runner_terminal_receipt_fk'
                   AND conrelid='amodex_environment_runner_jobs'::regclass) THEN
        ALTER TABLE amodex_environment_runner_jobs
            ADD CONSTRAINT runner_terminal_receipt_fk
            FOREIGN KEY (job_id,terminal_receipt_id)
            REFERENCES amodex_runner_terminal_receipts(job_id,receipt_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='runner_terminal_requires_receipt'
                   AND conrelid='amodex_environment_runner_jobs'::regclass) THEN
        ALTER TABLE amodex_environment_runner_jobs
            ADD CONSTRAINT runner_terminal_requires_receipt CHECK (
                execution_attempt_id IS NULL OR state NOT IN ('COMPLETED','FAILED','CANCELLED')
                OR terminal_receipt_id IS NOT NULL
            );
    END IF;
END $$;
