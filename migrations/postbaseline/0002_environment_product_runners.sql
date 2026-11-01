CREATE TABLE IF NOT EXISTS amodex_environment_runner_jobs (
    job_id UUID PRIMARY KEY,
    environment_id UUID NOT NULL REFERENCES amodex_environments(environment_id) ON DELETE CASCADE,
    session_id UUID NOT NULL,
    run_id UUID NOT NULL REFERENCES amodex_runs(run_id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES amodex_projects(project_id) ON DELETE CASCADE,
    engine TEXT NULL,
    runner_kind TEXT NOT NULL CHECK (runner_kind IN ('ACTIONS_HEADLESS','DESKTOP_STUDIO')),
    worker_profile TEXT NOT NULL,
    task_class TEXT NOT NULL CHECK (task_class IN ('MICRO','ROUTINE','STANDARD','COMPLEX','CRITICAL')),
    write_paths JSONB NOT NULL DEFAULT '[]'::jsonb,
    state TEXT NOT NULL CHECK (state IN ('QUEUED','DISPATCHED','DISPATCH_UNKNOWN','CLAIMED','RUNNING','COMPLETED','FAILED','CANCELLED')),
    branch_name TEXT NULL,
    result_commit TEXT NULL CHECK (result_commit IS NULL OR result_commit ~ '^[0-9a-f]{40}$'),
    summary TEXT NULL,
    github_run_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at TIMESTAMPTZ NULL,
    claimed_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    FOREIGN KEY (session_id, environment_id)
        REFERENCES amodex_sessions(session_id, environment_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_amodex_environment_runner_jobs_environment
    ON amodex_environment_runner_jobs(environment_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_amodex_environment_runner_jobs_run
    ON amodex_environment_runner_jobs(run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_amodex_environment_runner_jobs_state
    ON amodex_environment_runner_jobs(state, created_at);
