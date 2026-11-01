CREATE TABLE IF NOT EXISTS amodex_environments (
    environment_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    engine TEXT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','DISABLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amodex_environment_projects (
    environment_id UUID NOT NULL REFERENCES amodex_environments(environment_id) ON DELETE CASCADE,
    project_id UUID PRIMARY KEY REFERENCES amodex_projects(project_id) ON DELETE CASCADE,
    bound_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_amodex_environment_projects_environment
    ON amodex_environment_projects(environment_id, bound_at);

CREATE TABLE IF NOT EXISTS amodex_sessions (
    session_id UUID PRIMARY KEY,
    environment_id UUID NOT NULL REFERENCES amodex_environments(environment_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    domain_key TEXT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, environment_id)
);

CREATE INDEX IF NOT EXISTS idx_amodex_sessions_environment
    ON amodex_sessions(environment_id, created_at DESC);

CREATE TABLE IF NOT EXISTS amodex_session_runs (
    run_id UUID PRIMARY KEY REFERENCES amodex_runs(run_id) ON DELETE CASCADE,
    session_id UUID NOT NULL,
    environment_id UUID NOT NULL,
    bound_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (session_id, environment_id)
        REFERENCES amodex_sessions(session_id, environment_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_amodex_session_runs_environment
    ON amodex_session_runs(environment_id, bound_at DESC);

CREATE TABLE IF NOT EXISTS amodex_memories (
    memory_id UUID PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('OWNER','ENVIRONMENT','DOMAIN','SESSION')),
    environment_id UUID NULL REFERENCES amodex_environments(environment_id) ON DELETE CASCADE,
    session_id UUID NULL,
    domain_key TEXT NULL,
    content TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (session_id, environment_id)
        REFERENCES amodex_sessions(session_id, environment_id)
        ON DELETE CASCADE,
    CHECK (
        (scope='OWNER' AND environment_id IS NULL AND session_id IS NULL AND domain_key IS NULL)
        OR
        (scope='ENVIRONMENT' AND environment_id IS NOT NULL AND session_id IS NULL AND domain_key IS NULL)
        OR
        (scope='DOMAIN' AND environment_id IS NOT NULL AND session_id IS NULL AND domain_key IS NOT NULL)
        OR
        (scope='SESSION' AND environment_id IS NOT NULL AND session_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_amodex_memory_scope_digest
    ON amodex_memories(
        scope,
        COALESCE(environment_id, '00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(session_id, '00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(domain_key, ''),
        content_sha256
    );

CREATE INDEX IF NOT EXISTS idx_amodex_memories_environment_scope
    ON amodex_memories(environment_id, scope, created_at DESC);

CREATE TABLE IF NOT EXISTS amodex_environment_usage_events (
    usage_id UUID PRIMARY KEY,
    environment_id UUID NOT NULL REFERENCES amodex_environments(environment_id) ON DELETE CASCADE,
    session_id UUID NULL,
    run_id UUID NULL REFERENCES amodex_runs(run_id) ON DELETE SET NULL,
    task_id TEXT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens BIGINT NOT NULL CHECK (input_tokens >= 0),
    output_tokens BIGINT NOT NULL CHECK (output_tokens >= 0),
    requests INTEGER NOT NULL CHECK (requests >= 0),
    outcome TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (session_id, environment_id)
        REFERENCES amodex_sessions(session_id, environment_id)
        ON DELETE NO ACTION
);

CREATE INDEX IF NOT EXISTS idx_amodex_environment_usage_environment_created
    ON amodex_environment_usage_events(environment_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_amodex_environment_usage_provider_created
    ON amodex_environment_usage_events(provider, model, created_at DESC);

CREATE TABLE IF NOT EXISTS amodex_environment_budget_policies (
    environment_id UUID PRIMARY KEY REFERENCES amodex_environments(environment_id) ON DELETE CASCADE,
    policy JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
