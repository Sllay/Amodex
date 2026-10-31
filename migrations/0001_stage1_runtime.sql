CREATE TABLE IF NOT EXISTS amodex_runs (
    run_id UUID PRIMARY KEY,
    prompt TEXT NOT NULL,
    state TEXT NOT NULL,
    provider TEXT NULL,
    model TEXT NULL,
    result_text TEXT NULL,
    last_error_code TEXT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    event_seq BIGINT NOT NULL DEFAULT 0 CHECK (event_seq >= 0),
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS amodex_runs_claim_idx
    ON amodex_runs (state, lease_expires_at, created_at);

CREATE TABLE IF NOT EXISTS amodex_run_events (
    event_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES amodex_runs(run_id) ON DELETE CASCADE,
    seq BIGINT NOT NULL CHECK (seq > 0),
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS amodex_run_events_run_idx
    ON amodex_run_events (run_id, seq);
