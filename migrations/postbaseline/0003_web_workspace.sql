CREATE TABLE IF NOT EXISTS amodex_workspace_projects (
    workspace_id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    description TEXT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amodex_workspace_environments (
    workspace_id UUID NOT NULL REFERENCES amodex_workspace_projects(workspace_id) ON DELETE CASCADE,
    environment_id UUID NOT NULL REFERENCES amodex_environments(environment_id) ON DELETE RESTRICT,
    label TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, environment_id),
    UNIQUE (environment_id)
);

CREATE TABLE IF NOT EXISTS amodex_workspace_chats (
    chat_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES amodex_workspace_projects(workspace_id) ON DELETE CASCADE,
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','ARCHIVED')),
    active_environment_id UUID NULL REFERENCES amodex_environments(environment_id) ON DELETE SET NULL,
    rolling_summary TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amodex_workspace_messages (
    message_id UUID PRIMARY KEY,
    chat_id UUID NOT NULL REFERENCES amodex_workspace_chats(chat_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('OWNER','AMODEX','SYSTEM')),
    content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 200000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amodex_workspace_memories (
    memory_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES amodex_workspace_projects(workspace_id) ON DELETE CASCADE,
    content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 100000),
    source_ref TEXT NULL,
    status TEXT NOT NULL DEFAULT 'CANDIDATE' CHECK (status IN ('CANDIDATE','APPROVED','REJECTED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amodex_workspace_attachments (
    attachment_id UUID PRIMARY KEY,
    chat_id UUID NOT NULL REFERENCES amodex_workspace_chats(chat_id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 255),
    media_type TEXT NOT NULL CHECK (length(trim(media_type)) BETWEEN 1 AND 160),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT NULL CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    storage_ref TEXT NOT NULL CHECK (length(trim(storage_ref)) BETWEEN 1 AND 1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS amodex_execution_drafts (
    draft_id UUID PRIMARY KEY,
    chat_id UUID NOT NULL REFERENCES amodex_workspace_chats(chat_id) ON DELETE CASCADE,
    environment_id UUID NULL REFERENCES amodex_environments(environment_id) ON DELETE SET NULL,
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
    objective TEXT NOT NULL CHECK (length(objective) BETWEEN 1 AND 100000),
    instructions TEXT NOT NULL CHECK (length(instructions) BETWEEN 1 AND 200000),
    validation TEXT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','READY','EXECUTED','CANCELLED')),
    run_id UUID NULL REFERENCES amodex_runs(run_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workspace_chats_project
    ON amodex_workspace_chats(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workspace_messages_chat
    ON amodex_workspace_messages(chat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workspace_memories_project
    ON amodex_workspace_memories(workspace_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workspace_drafts_chat
    ON amodex_execution_drafts(chat_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workspace_attachments_chat
    ON amodex_workspace_attachments(chat_id, created_at);
