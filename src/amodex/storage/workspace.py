"""Durable Project/Chat/Memory/Draft storage for the web-first workspace."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from amodex.contracts.ids import new_uuid7
from amodex.workspace.models import (
    AttachmentRecord,
    ChatMessageRecord,
    ChatRecord,
    ChatRole,
    ExecutionDraftRecord,
    ExecutionDraftStatus,
    WorkspaceEnvironmentRecord,
    WorkspaceMemoryRecord,
    WorkspaceMemoryStatus,
    WorkspaceRecord,
    WorkspaceStatus,
)


class WorkspaceStateError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class PostgresWorkspaceRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    @property
    def dsn(self) -> str:
        return self._dsn

    def schema_ready(self) -> bool:
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(
                """
                SELECT
                  to_regclass('public.amodex_workspace_projects'),
                  to_regclass('public.amodex_workspace_chats'),
                  to_regclass('public.amodex_workspace_messages'),
                  to_regclass('public.amodex_execution_drafts')
                """
            ).fetchone()
        return bool(rows and all(rows))

    def create_workspace(self, *, name: str, description: str | None = None) -> WorkspaceRecord:
        workspace_id = new_uuid7()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                INSERT INTO amodex_workspace_projects(workspace_id,name,description,status,created_at,updated_at)
                VALUES (%s,%s,%s,'ACTIVE',NOW(),NOW()) RETURNING *
                """,
                (workspace_id, name.strip(), description.strip() if description else None),
            ).fetchone()
        assert row is not None
        return _workspace(row)

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM amodex_workspace_projects WHERE workspace_id=%s",
                (workspace_id,),
            ).fetchone()
        return _workspace(row) if row else None

    def list_workspaces(self, *, limit: int = 200) -> tuple[WorkspaceRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM amodex_workspace_projects ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return tuple(_workspace(row) for row in rows)

    def bind_environment(self, *, workspace_id: str, environment_id: str, label: str | None = None) -> WorkspaceEnvironmentRecord:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                workspace = conn.execute(
                    "SELECT status FROM amodex_workspace_projects WHERE workspace_id=%s FOR SHARE",
                    (workspace_id,),
                ).fetchone()
                if workspace is None:
                    raise KeyError(workspace_id)
                if workspace["status"] != WorkspaceStatus.ACTIVE.value:
                    raise WorkspaceStateError("WORKSPACE_INACTIVE", workspace_id)
                env = conn.execute(
                    "SELECT environment_id FROM amodex_environments WHERE environment_id=%s FOR SHARE",
                    (environment_id,),
                ).fetchone()
                if env is None:
                    raise WorkspaceStateError("ENVIRONMENT_NOT_FOUND", environment_id)
                try:
                    row = conn.execute(
                        """
                        INSERT INTO amodex_workspace_environments(workspace_id,environment_id,label,created_at)
                        VALUES (%s,%s,%s,NOW())
                        ON CONFLICT (workspace_id,environment_id) DO UPDATE SET label=EXCLUDED.label
                        RETURNING *
                        """,
                        (workspace_id, environment_id, label.strip() if label else None),
                    ).fetchone()
                except psycopg.errors.UniqueViolation as exc:
                    raise WorkspaceStateError(
                        "ENVIRONMENT_ALREADY_BOUND_TO_WORKSPACE", environment_id
                    ) from exc
                conn.execute(
                    "UPDATE amodex_workspace_projects SET updated_at=NOW() WHERE workspace_id=%s",
                    (workspace_id,),
                )
        assert row is not None
        return _workspace_environment(row)

    def list_environments(self, *, workspace_id: str) -> tuple[WorkspaceEnvironmentRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT * FROM amodex_workspace_environments
                WHERE workspace_id=%s ORDER BY created_at
                """,
                (workspace_id,),
            ).fetchall()
        return tuple(_workspace_environment(row) for row in rows)

    def create_chat(self, *, workspace_id: str, title: str, active_environment_id: str | None = None) -> ChatRecord:
        chat_id = new_uuid7()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                workspace = conn.execute(
                    "SELECT status FROM amodex_workspace_projects WHERE workspace_id=%s FOR SHARE",
                    (workspace_id,),
                ).fetchone()
                if workspace is None:
                    raise KeyError(workspace_id)
                if workspace["status"] != WorkspaceStatus.ACTIVE.value:
                    raise WorkspaceStateError("WORKSPACE_INACTIVE", workspace_id)
                if active_environment_id is not None:
                    bound = conn.execute(
                        """
                        SELECT 1 FROM amodex_workspace_environments
                        WHERE workspace_id=%s AND environment_id=%s
                        """,
                        (workspace_id, active_environment_id),
                    ).fetchone()
                    if bound is None:
                        raise WorkspaceStateError(
                            "CHAT_ENVIRONMENT_NOT_BOUND", active_environment_id
                        )
                row = conn.execute(
                    """
                    INSERT INTO amodex_workspace_chats(
                        chat_id,workspace_id,title,status,active_environment_id,created_at,updated_at
                    ) VALUES (%s,%s,%s,'ACTIVE',%s,NOW(),NOW()) RETURNING *
                    """,
                    (chat_id, workspace_id, title.strip(), active_environment_id),
                ).fetchone()
                conn.execute(
                    "UPDATE amodex_workspace_projects SET updated_at=NOW() WHERE workspace_id=%s",
                    (workspace_id,),
                )
        assert row is not None
        return _chat(row)

    def get_chat(self, chat_id: str) -> ChatRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute("SELECT * FROM amodex_workspace_chats WHERE chat_id=%s", (chat_id,)).fetchone()
        return _chat(row) if row else None

    def list_chats(self, *, workspace_id: str, limit: int = 300) -> tuple[ChatRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT * FROM amodex_workspace_chats
                WHERE workspace_id=%s ORDER BY updated_at DESC LIMIT %s
                """,
                (workspace_id, limit),
            ).fetchall()
        return tuple(_chat(row) for row in rows)

    def add_message(self, *, chat_id: str, role: ChatRole, content: str) -> ChatMessageRecord:
        message_id = new_uuid7()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                chat = conn.execute(
                    "SELECT status,workspace_id FROM amodex_workspace_chats WHERE chat_id=%s FOR UPDATE",
                    (chat_id,),
                ).fetchone()
                if chat is None:
                    raise KeyError(chat_id)
                if chat["status"] != "ACTIVE":
                    raise WorkspaceStateError("CHAT_INACTIVE", chat_id)
                row = conn.execute(
                    """
                    INSERT INTO amodex_workspace_messages(message_id,chat_id,role,content,created_at)
                    VALUES (%s,%s,%s,%s,NOW()) RETURNING *
                    """,
                    (message_id, chat_id, role.value, content),
                ).fetchone()
                conn.execute(
                    "UPDATE amodex_workspace_chats SET updated_at=NOW() WHERE chat_id=%s",
                    (chat_id,),
                )
                conn.execute(
                    "UPDATE amodex_workspace_projects SET updated_at=NOW() WHERE workspace_id=%s",
                    (chat["workspace_id"],),
                )
        assert row is not None
        return _message(row)

    def list_messages(self, *, chat_id: str, limit: int = 500) -> tuple[ChatMessageRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT * FROM amodex_workspace_messages
                WHERE chat_id=%s ORDER BY created_at ASC LIMIT %s
                """,
                (chat_id, limit),
            ).fetchall()
        return tuple(_message(row) for row in rows)

    def create_memory(self, *, workspace_id: str, content: str, source_ref: str | None = None) -> WorkspaceMemoryRecord:
        memory_id = new_uuid7()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                INSERT INTO amodex_workspace_memories(
                    memory_id,workspace_id,content,source_ref,status,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,'CANDIDATE',NOW(),NOW()) RETURNING *
                """,
                (memory_id, workspace_id, content, source_ref),
            ).fetchone()
        assert row is not None
        return _memory(row)

    def set_memory_status(self, *, memory_id: str, status: WorkspaceMemoryStatus) -> WorkspaceMemoryRecord:
        if status == WorkspaceMemoryStatus.CANDIDATE:
            raise WorkspaceStateError("MEMORY_DECISION_REQUIRED", "use APPROVED or REJECTED")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                UPDATE amodex_workspace_memories SET status=%s,updated_at=NOW()
                WHERE memory_id=%s RETURNING *
                """,
                (status.value, memory_id),
            ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return _memory(row)

    def list_memories(self, *, workspace_id: str, include_rejected: bool = False) -> tuple[WorkspaceMemoryRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            if include_rejected:
                rows = conn.execute(
                    "SELECT * FROM amodex_workspace_memories WHERE workspace_id=%s ORDER BY updated_at DESC",
                    (workspace_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM amodex_workspace_memories
                    WHERE workspace_id=%s AND status<>'REJECTED' ORDER BY updated_at DESC
                    """,
                    (workspace_id,),
                ).fetchall()
        return tuple(_memory(row) for row in rows)

    def create_attachment(self, *, chat_id: str, name: str, media_type: str, size_bytes: int, storage_ref: str, sha256: str | None = None) -> AttachmentRecord:
        attachment_id = new_uuid7()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            chat = conn.execute("SELECT 1 FROM amodex_workspace_chats WHERE chat_id=%s", (chat_id,)).fetchone()
            if chat is None:
                raise KeyError(chat_id)
            row = conn.execute(
                """
                INSERT INTO amodex_workspace_attachments(
                    attachment_id,chat_id,name,media_type,size_bytes,sha256,storage_ref,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW()) RETURNING *
                """,
                (attachment_id, chat_id, name, media_type, size_bytes, sha256, storage_ref),
            ).fetchone()
        assert row is not None
        return _attachment(row)

    def list_attachments(self, *, chat_id: str) -> tuple[AttachmentRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM amodex_workspace_attachments WHERE chat_id=%s ORDER BY created_at",
                (chat_id,),
            ).fetchall()
        return tuple(_attachment(row) for row in rows)

    def create_draft(
        self,
        *,
        chat_id: str,
        title: str,
        objective: str,
        instructions: str,
        validation: str | None = None,
        environment_id: str | None = None,
    ) -> ExecutionDraftRecord:
        draft_id = new_uuid7()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                chat = conn.execute(
                    "SELECT workspace_id,status FROM amodex_workspace_chats WHERE chat_id=%s FOR SHARE",
                    (chat_id,),
                ).fetchone()
                if chat is None:
                    raise KeyError(chat_id)
                if chat["status"] != "ACTIVE":
                    raise WorkspaceStateError("CHAT_INACTIVE", chat_id)
                if environment_id is not None:
                    bound = conn.execute(
                        """
                        SELECT 1 FROM amodex_workspace_environments
                        WHERE workspace_id=%s AND environment_id=%s
                        """,
                        (chat["workspace_id"], environment_id),
                    ).fetchone()
                    if bound is None:
                        raise WorkspaceStateError("DRAFT_ENVIRONMENT_NOT_BOUND", environment_id)
                row = conn.execute(
                    """
                    INSERT INTO amodex_execution_drafts(
                        draft_id,chat_id,environment_id,title,objective,instructions,validation,status,created_at,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,'DRAFT',NOW(),NOW()) RETURNING *
                    """,
                    (draft_id, chat_id, environment_id, title, objective, instructions, validation),
                ).fetchone()
        assert row is not None
        return _draft(row)

    def set_draft_status(self, *, draft_id: str, status: ExecutionDraftStatus) -> ExecutionDraftRecord:
        if status == ExecutionDraftStatus.EXECUTED:
            raise WorkspaceStateError(
                "DRAFT_EXECUTION_REQUIRES_RUN_HANDOFF",
                "chat/draft product cannot mark execution complete without a real Run",
            )
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                UPDATE amodex_execution_drafts SET status=%s,updated_at=NOW()
                WHERE draft_id=%s AND status<>'EXECUTED' RETURNING *
                """,
                (status.value, draft_id),
            ).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return _draft(row)

    def list_drafts(self, *, chat_id: str) -> tuple[ExecutionDraftRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM amodex_execution_drafts WHERE chat_id=%s ORDER BY updated_at DESC",
                (chat_id,),
            ).fetchall()
        return tuple(_draft(row) for row in rows)


def _workspace(row: dict[str, Any]) -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_id=str(row["workspace_id"]), name=row["name"], description=row["description"],
        status=WorkspaceStatus(row["status"]), created_at=row["created_at"], updated_at=row["updated_at"]
    )


def _workspace_environment(row: dict[str, Any]) -> WorkspaceEnvironmentRecord:
    return WorkspaceEnvironmentRecord(
        workspace_id=str(row["workspace_id"]), environment_id=str(row["environment_id"]),
        label=row["label"], created_at=row["created_at"]
    )


def _chat(row: dict[str, Any]) -> ChatRecord:
    return ChatRecord(
        chat_id=str(row["chat_id"]), workspace_id=str(row["workspace_id"]), title=row["title"],
        status=row["status"], active_environment_id=str(row["active_environment_id"]) if row["active_environment_id"] else None,
        rolling_summary=row["rolling_summary"], created_at=row["created_at"], updated_at=row["updated_at"]
    )


def _message(row: dict[str, Any]) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=str(row["message_id"]), chat_id=str(row["chat_id"]), role=ChatRole(row["role"]),
        content=row["content"], created_at=row["created_at"]
    )


def _memory(row: dict[str, Any]) -> WorkspaceMemoryRecord:
    return WorkspaceMemoryRecord(
        memory_id=str(row["memory_id"]), workspace_id=str(row["workspace_id"]), content=row["content"],
        source_ref=row["source_ref"], status=WorkspaceMemoryStatus(row["status"]),
        created_at=row["created_at"], updated_at=row["updated_at"]
    )


def _attachment(row: dict[str, Any]) -> AttachmentRecord:
    return AttachmentRecord(
        attachment_id=str(row["attachment_id"]), chat_id=str(row["chat_id"]), name=row["name"],
        media_type=row["media_type"], size_bytes=int(row["size_bytes"]), sha256=row["sha256"],
        storage_ref=row["storage_ref"], created_at=row["created_at"]
    )


def _draft(row: dict[str, Any]) -> ExecutionDraftRecord:
    return ExecutionDraftRecord(
        draft_id=str(row["draft_id"]), chat_id=str(row["chat_id"]),
        environment_id=str(row["environment_id"]) if row["environment_id"] else None,
        title=row["title"], objective=row["objective"], instructions=row["instructions"],
        validation=row["validation"], status=ExecutionDraftStatus(row["status"]),
        run_id=str(row["run_id"]) if row["run_id"] else None,
        created_at=row["created_at"], updated_at=row["updated_at"]
    )
