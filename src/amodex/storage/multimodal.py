"""Durable storage for Step 3 multimodal attachment links and operations."""

from __future__ import annotations

from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from amodex.contracts.ids import new_uuid7
from amodex.multimodal.models import (
    MessageAttachmentRecord,
    MultimodalOperationKind,
    MultimodalOperationRecord,
    MultimodalOperationStatus,
)
from amodex.workspace.models import AttachmentRecord
from amodex.storage.workspace import WorkspaceStateError


class PostgresMultimodalRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    @property
    def dsn(self) -> str:
        return self._dsn

    def schema_ready(self) -> bool:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                """
                SELECT
                  to_regclass('public.amodex_workspace_message_attachments'),
                  to_regclass('public.amodex_multimodal_operations')
                """
            ).fetchone()
        return bool(row and all(row))

    def get_attachment(self, attachment_id: str) -> AttachmentRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM amodex_workspace_attachments WHERE attachment_id=%s",
                (attachment_id,),
            ).fetchone()
        return _attachment(row) if row else None

    def link_attachments(
        self,
        *,
        message_id: str,
        attachment_ids: Iterable[str],
    ) -> tuple[MessageAttachmentRecord, ...]:
        ids = tuple(dict.fromkeys(str(item) for item in attachment_ids))
        if not ids:
            return ()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                message = conn.execute(
                    "SELECT message_id,chat_id FROM amodex_workspace_messages WHERE message_id=%s FOR SHARE",
                    (message_id,),
                ).fetchone()
                if message is None:
                    raise KeyError(message_id)
                chat_id = str(message["chat_id"])
                result: list[MessageAttachmentRecord] = []
                for attachment_id in ids:
                    attachment = conn.execute(
                        "SELECT * FROM amodex_workspace_attachments WHERE attachment_id=%s FOR SHARE",
                        (attachment_id,),
                    ).fetchone()
                    if attachment is None:
                        raise KeyError(attachment_id)
                    if str(attachment["chat_id"]) != chat_id:
                        raise WorkspaceStateError(
                            "ATTACHMENT_MESSAGE_CHAT_MISMATCH",
                            f"attachment {attachment_id} does not belong to message chat",
                        )
                    link = conn.execute(
                        """
                        INSERT INTO amodex_workspace_message_attachments(
                            message_id,chat_id,attachment_id,created_at
                        ) VALUES (%s,%s,%s,NOW())
                        ON CONFLICT (message_id,attachment_id) DO UPDATE SET chat_id=EXCLUDED.chat_id
                        RETURNING created_at
                        """,
                        (message_id, chat_id, attachment_id),
                    ).fetchone()
                    assert link is not None
                    result.append(
                        MessageAttachmentRecord(
                            message_id=message_id,
                            chat_id=chat_id,
                            attachment=_attachment(attachment),
                            created_at=link["created_at"],
                        )
                    )
        return tuple(result)

    def list_attachment_links(self, *, chat_id: str) -> tuple[MessageAttachmentRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT l.message_id,l.chat_id,l.created_at,
                       a.attachment_id,a.name,a.media_type,a.size_bytes,a.sha256,a.storage_ref,a.created_at AS attachment_created_at
                FROM amodex_workspace_message_attachments l
                JOIN amodex_workspace_attachments a ON a.attachment_id=l.attachment_id
                WHERE l.chat_id=%s
                ORDER BY l.created_at,a.created_at
                """,
                (chat_id,),
            ).fetchall()
        return tuple(_message_attachment(row) for row in rows)

    def create_operation(
        self,
        *,
        chat_id: str,
        kind: MultimodalOperationKind,
        attachment_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        billing_class: str | None = None,
        input_text: str | None = None,
        provenance: dict[str, object] | None = None,
    ) -> MultimodalOperationRecord:
        operation_id = new_uuid7()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            chat = conn.execute(
                "SELECT 1 FROM amodex_workspace_chats WHERE chat_id=%s",
                (chat_id,),
            ).fetchone()
            if chat is None:
                raise KeyError(chat_id)
            if attachment_id is not None:
                attachment = conn.execute(
                    "SELECT chat_id FROM amodex_workspace_attachments WHERE attachment_id=%s",
                    (attachment_id,),
                ).fetchone()
                if attachment is None:
                    raise KeyError(attachment_id)
                if str(attachment["chat_id"]) != chat_id:
                    raise WorkspaceStateError("MULTIMODAL_ATTACHMENT_CHAT_MISMATCH", attachment_id)
            row = conn.execute(
                """
                INSERT INTO amodex_multimodal_operations(
                    operation_id,chat_id,kind,status,attachment_id,provider,model,billing_class,
                    input_text,provenance,created_at,updated_at
                ) VALUES (%s,%s,%s,'RUNNING',%s,%s,%s,%s,%s,%s::jsonb,NOW(),NOW())
                RETURNING *
                """,
                (
                    operation_id,
                    chat_id,
                    kind.value,
                    attachment_id,
                    provider,
                    model,
                    billing_class,
                    input_text,
                    psycopg.types.json.Jsonb(provenance or {}),
                ),
            ).fetchone()
        assert row is not None
        return _operation(row)

    def finish_operation(
        self,
        *,
        operation_id: str,
        status: MultimodalOperationStatus,
        result_text: str | None = None,
        output_attachment_id: str | None = None,
        error_code: str | None = None,
        provenance: dict[str, object] | None = None,
    ) -> MultimodalOperationRecord:
        if status == MultimodalOperationStatus.RUNNING:
            raise ValueError("finish_operation requires a terminal status")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                current = conn.execute(
                    "SELECT chat_id,provenance FROM amodex_multimodal_operations WHERE operation_id=%s FOR UPDATE",
                    (operation_id,),
                ).fetchone()
                if current is None:
                    raise KeyError(operation_id)
                if output_attachment_id is not None:
                    output = conn.execute(
                        "SELECT chat_id FROM amodex_workspace_attachments WHERE attachment_id=%s",
                        (output_attachment_id,),
                    ).fetchone()
                    if output is None:
                        raise KeyError(output_attachment_id)
                    if str(output["chat_id"]) != str(current["chat_id"]):
                        raise WorkspaceStateError(
                            "MULTIMODAL_OUTPUT_CHAT_MISMATCH", output_attachment_id
                        )
                merged = dict(current["provenance"] or {})
                if provenance:
                    merged.update(provenance)
                row = conn.execute(
                    """
                    UPDATE amodex_multimodal_operations
                    SET status=%s,result_text=%s,output_attachment_id=%s,error_code=%s,
                        provenance=%s::jsonb,updated_at=NOW()
                    WHERE operation_id=%s RETURNING *
                    """,
                    (
                        status.value,
                        result_text,
                        output_attachment_id,
                        error_code,
                        psycopg.types.json.Jsonb(merged),
                        operation_id,
                    ),
                ).fetchone()
        assert row is not None
        return _operation(row)

    def list_operations(self, *, chat_id: str, limit: int = 200) -> tuple[MultimodalOperationRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT * FROM amodex_multimodal_operations
                WHERE chat_id=%s ORDER BY created_at DESC LIMIT %s
                """,
                (chat_id, limit),
            ).fetchall()
        return tuple(_operation(row) for row in rows)


def _attachment(row: dict[str, Any]) -> AttachmentRecord:
    return AttachmentRecord(
        attachment_id=str(row["attachment_id"]),
        chat_id=str(row["chat_id"]),
        name=row["name"],
        media_type=row["media_type"],
        size_bytes=int(row["size_bytes"]),
        sha256=row["sha256"],
        storage_ref=row["storage_ref"],
        created_at=row.get("attachment_created_at", row["created_at"]),
    )


def _message_attachment(row: dict[str, Any]) -> MessageAttachmentRecord:
    return MessageAttachmentRecord(
        message_id=str(row["message_id"]),
        chat_id=str(row["chat_id"]),
        attachment=_attachment(row),
        created_at=row["created_at"],
    )


def _operation(row: dict[str, Any]) -> MultimodalOperationRecord:
    return MultimodalOperationRecord(
        operation_id=str(row["operation_id"]),
        chat_id=str(row["chat_id"]),
        kind=MultimodalOperationKind(row["kind"]),
        status=MultimodalOperationStatus(row["status"]),
        attachment_id=str(row["attachment_id"]) if row["attachment_id"] else None,
        output_attachment_id=str(row["output_attachment_id"]) if row["output_attachment_id"] else None,
        provider=row["provider"],
        model=row["model"],
        billing_class=row["billing_class"],
        input_text=row["input_text"],
        result_text=row["result_text"],
        error_code=row["error_code"],
        provenance=dict(row["provenance"] or {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
