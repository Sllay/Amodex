CREATE TABLE IF NOT EXISTS amodex_projects (
    project_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    repo_url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','DISABLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE amodex_runs
    ADD COLUMN IF NOT EXISTS project_id UUID NULL REFERENCES amodex_projects(project_id);

CREATE INDEX IF NOT EXISTS idx_amodex_runs_project_created
    ON amodex_runs(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS amodex_run_controls (
    run_id UUID PRIMARY KEY REFERENCES amodex_runs(run_id) ON DELETE CASCADE,
    desired_state TEXT NOT NULL CHECK (desired_state IN ('ACTIVE','PAUSED','CANCELLED')),
    reason TEXT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amodex_approvals (
    approval_id UUID PRIMARY KEY,
    action TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    impact TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','APPROVED','REJECTED','EXPIRED')),
    requested_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ NULL,
    expires_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_amodex_approvals_status_requested
    ON amodex_approvals(status, requested_at DESC);

CREATE TABLE IF NOT EXISTS amodex_provider_connections (
    provider TEXT PRIMARY KEY,
    credential_pool_id TEXT NOT NULL,
    secret_ref TEXT NOT NULL,
    billing_class TEXT NOT NULL CHECK (billing_class IN ('free','paid','unknown')),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amodex_runners (
    runner_id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('STARTING','RUNNING','DEGRADED','OFFLINE','COMPLETED','FAILED','CANCELLED')),
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_heartbeat_at TIMESTAMPTZ NOT NULL,
    detail TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_amodex_runners_heartbeat
    ON amodex_runners(last_heartbeat_at DESC);

CREATE TABLE IF NOT EXISTS amodex_task_views (
    run_id UUID NOT NULL REFERENCES amodex_runs(run_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    state TEXT NOT NULL,
    dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    worker_profile TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, task_id)
);

CREATE TABLE IF NOT EXISTS amodex_evidence_views (
    evidence_id TEXT PRIMARY KEY,
    run_id UUID NULL REFERENCES amodex_runs(run_id) ON DELETE SET NULL,
    task_id TEXT NOT NULL,
    subject_sha TEXT NOT NULL CHECK (subject_sha ~ '^[0-9a-f]{40}$'),
    validator_id TEXT NOT NULL,
    result TEXT NOT NULL,
    summary TEXT NOT NULL,
    artifact_sha256 JSONB NOT NULL DEFAULT '[]'::jsonb,
    observed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS amodex_checkpoint_views (
    checkpoint_id TEXT PRIMARY KEY,
    run_id UUID NULL REFERENCES amodex_runs(run_id) ON DELETE SET NULL,
    task_id TEXT NOT NULL,
    checkpoint_class TEXT NOT NULL,
    commit_sha TEXT NOT NULL CHECK (commit_sha ~ '^[0-9a-f]{40}$'),
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS amodex_audit_events (
    audit_id UUID PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_amodex_audit_created
    ON amodex_audit_events(created_at DESC);

CREATE TABLE IF NOT EXISTS amodex_product_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO amodex_product_settings(key, value)
VALUES
    ('retention_policy', '{"terminal_run_days":30,"audit_days":90}'::jsonb),
    ('zero_cost_policy', '{"paid_inference_allowed":false,"auto_upgrade_to_paid":false}'::jsonb)
ON CONFLICT (key) DO NOTHING;
