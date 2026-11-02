"""Bounded-memory attachment upload route for the web workspace."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path, PurePath
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request

from amodex.multimodal.object_store import (
    MediaObjectStore,
    MediaStoreError,
    MediaStoreUnavailable,
)
from amodex.multimodal.streaming_store import put_media_file
from amodex.storage.workspace import PostgresWorkspaceRepository, WorkspaceStateError
from amodex.workspace.models import AttachmentRecord


logger = logging.getLogger("amodex.multimodal.upload")


def _safe_name(raw: str) -> str:
    candidate = unquote(raw or "").replace("\\", "/")
    candidate = PurePath(candidate).name
    candidate = "".join(
        ch for ch in candidate if ch >= " " and ch not in {"\x7f", "\r", "\n"}
    ).strip()
    return (candidate or "upload.bin")[:255]


def build_streaming_upload_router(
    *,
    workspace_repository: PostgresWorkspaceRepository,
    media_store: MediaObjectStore,
    max_upload_bytes: int,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["multimodal"])

    @router.post("/chats/{chat_id}/uploads", response_model=AttachmentRecord, status_code=201)
    async def upload_attachment(chat_id: str, request: Request) -> AttachmentRecord:
        if workspace_repository.get_chat(chat_id) is None:
            raise HTTPException(status_code=404, detail="chat not found")
        if not media_store.configured:
            raise HTTPException(
                status_code=503,
                detail={"code": "MEDIA_STORE_NOT_CONFIGURED", "message": "configure media storage"},
            )

        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
            if length > max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "UPLOAD_TOO_LARGE", "max_upload_bytes": max_upload_bytes},
                )

        fd, tmp_name = tempfile.mkstemp(prefix="amodex-upload-", suffix=".bin")
        size_bytes = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    size_bytes += len(chunk)
                    if size_bytes > max_upload_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail={
                                "code": "UPLOAD_TOO_LARGE",
                                "max_upload_bytes": max_upload_bytes,
                            },
                        )
                    handle.write(chunk)
                if size_bytes == 0:
                    raise HTTPException(status_code=400, detail={"code": "EMPTY_UPLOAD"})
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)

            name = _safe_name(request.headers.get("x-file-name", "upload.bin"))
            media_type = (
                request.headers.get("content-type", "application/octet-stream")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if not media_type or len(media_type) > 160:
                media_type = "application/octet-stream"

            stored = put_media_file(media_store, Path(tmp_name))
            return workspace_repository.create_attachment(
                chat_id=chat_id,
                name=name,
                media_type=media_type,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                storage_ref=stored.storage_ref,
            )
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="multimodal resource not found") from exc
        except WorkspaceStateError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": exc.detail},
            ) from exc
        except MediaStoreUnavailable as exc:
            logger.warning(
                "media_store_unavailable chat_id=%s size_bytes=%s reason=%s",
                chat_id,
                size_bytes,
                exc,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "MEDIA_STORE_UNAVAILABLE",
                    "message": str(exc),
                },
            ) from exc
        except MediaStoreError as exc:
            # Storage integrity/provider failures are server-side upstream errors,
            # not client conflicts. Mapping them to 409 previously hid the real
            # Cloudinary failure and made the browser report a misleading conflict.
            logger.error(
                "media_store_error chat_id=%s size_bytes=%s reason=%s",
                chat_id,
                size_bytes,
                exc,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "MEDIA_STORE_ERROR",
                    "message": str(exc),
                },
            ) from exc
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    return router
