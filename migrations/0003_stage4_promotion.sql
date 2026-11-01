CREATE TABLE IF NOT EXISTS amodex_candidates (
    candidate_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    validated_checkpoint_id TEXT NOT NULL,
    source_sha TEXT NOT NULL CHECK (source_sha ~ '^[0-9a-f]{40}$'),
    expected_target_ref TEXT NOT NULL,
    expected_target_sha TEXT NOT NULL CHECK (expected_target_sha ~ '^[0-9a-f]{40}$'),
    changed_paths JSONB NOT NULL,
    dependency_paths JSONB NOT NULL,
    evidence_ids JSONB NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (validated_checkpoint_id, expected_target_ref, expected_target_sha)
);

CREATE TABLE IF NOT EXISTS amodex_promotions (
    promotion_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES amodex_candidates(candidate_id),
    idempotency_key TEXT NOT NULL UNIQUE,
    target_ref TEXT NOT NULL,
    expected_target_sha TEXT NOT NULL CHECK (expected_target_sha ~ '^[0-9a-f]{40}$'),
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    integration_sha TEXT NULL CHECK (integration_sha IS NULL OR integration_sha ~ '^[0-9a-f]{40}$'),
    target_post_sha TEXT NULL CHECK (target_post_sha IS NULL OR target_post_sha ~ '^[0-9a-f]{40}$'),
    integration_evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    reconciled_at TIMESTAMPTZ NULL,
    detail TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_amodex_candidates_task_id
    ON amodex_candidates(task_id);

CREATE INDEX IF NOT EXISTS idx_amodex_promotions_candidate_id
    ON amodex_promotions(candidate_id);
