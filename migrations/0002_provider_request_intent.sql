ALTER TABLE amodex_runs
    ADD COLUMN IF NOT EXISTS provider_request_key UUID NULL,
    ADD COLUMN IF NOT EXISTS provider_request_state TEXT NOT NULL DEFAULT 'NONE'
        CHECK (provider_request_state IN ('NONE', 'STARTED', 'COMPLETED'));
