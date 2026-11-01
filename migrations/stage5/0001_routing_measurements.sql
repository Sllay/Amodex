CREATE TABLE IF NOT EXISTS amodex_model_measurements (
    measurement_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    sample_count INTEGER NOT NULL CHECK (sample_count > 0),
    structured_tool_reliability DOUBLE PRECISION NOT NULL CHECK (structured_tool_reliability BETWEEN 0 AND 1),
    coding_pass_rate DOUBLE PRECISION NOT NULL CHECK (coding_pass_rate BETWEEN 0 AND 1),
    correction_success_rate DOUBLE PRECISION NOT NULL CHECK (correction_success_rate BETWEEN 0 AND 1),
    planning_schema_compliance DOUBLE PRECISION NOT NULL CHECK (planning_schema_compliance BETWEEN 0 AND 1),
    research_success_rate DOUBLE PRECISION NOT NULL CHECK (research_success_rate BETWEEN 0 AND 1),
    review_detection_rate DOUBLE PRECISION NOT NULL CHECK (review_detection_rate BETWEEN 0 AND 1),
    vision_success_rate DOUBLE PRECISION NULL CHECK (vision_success_rate IS NULL OR vision_success_rate BETWEEN 0 AND 1),
    latency_ms_p50 DOUBLE PRECISION NOT NULL CHECK (latency_ms_p50 >= 0),
    observed_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    UNIQUE (provider, model, observed_at, source)
);

CREATE INDEX IF NOT EXISTS idx_amodex_model_measurements_latest
    ON amodex_model_measurements(provider, model, observed_at DESC);

CREATE TABLE IF NOT EXISTS amodex_provider_availability_observations (
    availability_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    credential_pool_id TEXT NOT NULL,
    billing_class TEXT NOT NULL CHECK (billing_class IN ('free','paid','unknown')),
    health TEXT NOT NULL CHECK (health IN ('HEALTHY','DEGRADED','LIMITED','UNAVAILABLE','DISABLED_POLICY')),
    quota_available BOOLEAN NOT NULL,
    free_capacity_ratio DOUBLE PRECISION NOT NULL CHECK (free_capacity_ratio BETWEEN 0 AND 1),
    observed_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL,
    UNIQUE (provider, credential_pool_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_amodex_provider_availability_latest
    ON amodex_provider_availability_observations(provider, credential_pool_id, observed_at DESC);
