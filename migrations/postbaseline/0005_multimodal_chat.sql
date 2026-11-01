CREATE TABLE IF NOT EXISTS amodex_workspace_message_attachments (
    message_id UUID NOT NULL REFERENCES amodex_workspace_messages(message_id) ON DELETE CASCADE,
    chat_id UUID NOT NULL REFERENCES amodex_workspace_chats(chat_id) ON DELETE CASCADE,
    attachment_id UUID NOT NULL REFERENCES amodex_workspace_attachments(attachment_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (message_id, attachment_id)
);

CREATE TABLE IF NOT EXISTS amodex_multimodal_operations (
    operation_id UUID PRIMARY KEY,
    chat_id UUID NOT NULL REFERENCES amodex_workspace_chats(chat_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('VISION','STT','TTS','WEB_RESEARCH')),
    status TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','BLOCKED')),
    attachment_id UUID NULL REFERENCES amodex_workspace_attachments(attachment_id) ON DELETE SET NULL,
    output_attachment_id UUID NULL REFERENCES amodex_workspace_attachments(attachment_id) ON DELETE SET NULL,
    provider TEXT NULL,
    model TEXT NULL,
    billing_class TEXT NULL,
    input_text TEXT NULL,
    result_text TEXT NULL,
    error_code TEXT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workspace_message_attachments_chat
    ON amodex_workspace_message_attachments(chat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workspace_message_attachments_attachment
    ON amodex_workspace_message_attachments(attachment_id);
CREATE INDEX IF NOT EXISTS idx_multimodal_operations_chat
    ON amodex_multimodal_operations(chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_multimodal_operations_status
    ON amodex_multimodal_operations(status, updated_at DESC);
