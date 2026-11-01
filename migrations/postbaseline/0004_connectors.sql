CREATE TABLE IF NOT EXISTS amodex_connector_accounts (
    connector_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES amodex_workspace_projects(workspace_id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('GITHUB','ROBLOX')),
    external_account_id TEXT NULL,
    external_account_name TEXT NULL,
    credential_ref TEXT NULL,
    state TEXT NOT NULL DEFAULT 'CONNECTING' CHECK (state IN ('CONNECTING','CONNECTED','REVOKED','ERROR')),
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_connector_account_identity
    ON amodex_connector_accounts(workspace_id, provider, external_account_id)
    WHERE external_account_id IS NOT NULL AND state <> 'REVOKED';

CREATE TABLE IF NOT EXISTS amodex_connector_resources (
    resource_id UUID PRIMARY KEY,
    connector_id UUID NOT NULL REFERENCES amodex_connector_accounts(connector_id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('GITHUB_REPOSITORY','ROBLOX_UNIVERSE','ROBLOX_PLACE')),
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NULL,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    bound_environment_id UUID NULL REFERENCES amodex_environments(environment_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (connector_id, resource_type, external_id)
);

CREATE TABLE IF NOT EXISTS amodex_connector_oauth_states (
    state_hash TEXT PRIMARY KEY CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    workspace_id UUID NOT NULL REFERENCES amodex_workspace_projects(workspace_id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('GITHUB','ROBLOX')),
    verifier_ref TEXT NOT NULL,
    requested_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_connector_accounts_workspace
    ON amodex_connector_accounts(workspace_id, provider, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_connector_resources_account
    ON amodex_connector_resources(connector_id, resource_type, name);
CREATE INDEX IF NOT EXISTS idx_connector_oauth_expiry
    ON amodex_connector_oauth_states(expires_at) WHERE used_at IS NULL;
