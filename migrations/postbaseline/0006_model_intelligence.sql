CREATE TABLE IF NOT EXISTS amodex_model_routes (
    route_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    credential_pool_id TEXT NOT NULL,
    cost_class TEXT NOT NULL CHECK (cost_class IN ('free','paid','unknown')),
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    context_capacity_tokens INTEGER NOT NULL CHECK (context_capacity_tokens > 0),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('DISCOVERED','PROFILED_DECLARED','BENCHMARK_PENDING','ELIGIBLE_LIMITED','ELIGIBLE')),
    provenance TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amodex_model_benchmark_runs (
    benchmark_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES amodex_model_routes(route_id) ON DELETE RESTRICT,
    suite_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN_OUTCOME','BLOCKED')),
    sample_count INTEGER NOT NULL DEFAULT 0 CHECK (sample_count >= 0),
    passed_count INTEGER NOT NULL DEFAULT 0 CHECK (passed_count >= 0),
    aggregate_score DOUBLE PRECISION,
    latency_ms_p50 DOUBLE PRECISION,
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    progress_per_1k_tokens DOUBLE PRECISION,
    error_code TEXT,
    error_message TEXT,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_amodex_model_benchmark_route_time
    ON amodex_model_benchmark_runs(route_id, started_at DESC);

CREATE TABLE IF NOT EXISTS amodex_model_benchmark_cases (
    benchmark_case_id TEXT PRIMARY KEY,
    benchmark_id TEXT NOT NULL REFERENCES amodex_model_benchmark_runs(benchmark_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    passed BOOLEAN NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL CHECK (latency_ms >= 0),
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    result_excerpt TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (benchmark_id, case_id)
);

CREATE TABLE IF NOT EXISTS amodex_model_lifecycle_events (
    lifecycle_event_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES amodex_model_routes(route_id) ON DELETE RESTRICT,
    from_lifecycle TEXT NOT NULL,
    to_lifecycle TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    evidence_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_amodex_model_lifecycle_route_time
    ON amodex_model_lifecycle_events(route_id, created_at DESC);

CREATE TABLE IF NOT EXISTS amodex_model_route_decisions (
    decision_id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES amodex_workspace_chats(chat_id) ON DELETE CASCADE,
    owner_message_id TEXT NOT NULL UNIQUE REFERENCES amodex_workspace_messages(message_id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN_OUTCOME','BLOCKED')),
    selected_route_id TEXT REFERENCES amodex_model_routes(route_id) ON DELETE RESTRICT,
    final_route_id TEXT REFERENCES amodex_model_routes(route_id) ON DELETE RESTRICT,
    ranked_route_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    rejected_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    selection_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 2),
    fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
    result_message_id TEXT REFERENCES amodex_workspace_messages(message_id) ON DELETE SET NULL,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_amodex_model_decision_chat_time
    ON amodex_model_route_decisions(chat_id, created_at DESC);

CREATE TABLE IF NOT EXISTS amodex_model_route_attempts (
    attempt_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES amodex_model_route_decisions(decision_id) ON DELETE CASCADE,
    route_id TEXT NOT NULL REFERENCES amodex_model_routes(route_id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK (attempt_number BETWEEN 1 AND 2),
    request_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN_OUTCOME')),
    latency_ms DOUBLE PRECISION,
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    error_code TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (decision_id, attempt_number)
);
CREATE INDEX IF NOT EXISTS idx_amodex_model_attempt_route_time
    ON amodex_model_route_attempts(route_id, started_at DESC);
