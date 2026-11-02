"""Bounded-memory attachment upload route for the web workspace."""

from __future__ import annotations

import logging
import os
import re
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

_SAFE_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,16}$")
_MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/heic": "heic",
    "image/heif": "heif",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "application/pdf": "pdf",
    "application/json": "json",
    "text/plain": "txt",
    "text/csv": "csv",
}
# Cloudinary rejects these raw extensions by default for security. Do not hide an
# explicitly dangerous filename behind a different suffix just to force upload.
_BLOCKED_RAW_EXTENSIONS = {
    "action", "apk", "app", "bat", "bin", "cmd", "com", "command", "cpl",
    "csh", "exe", "gadget", "inf1", "ins", "inx", "ipa", "isu", "job", "jse",
    "ksh", "lnk", "msc", "msi", "msp", "mst", "osx", "out", "paf", "pif",
    "prg", "ps1", "reg", "rgs", "run", "sct", "shb", "shs", "u3p", "vb",
    "vbe", "vbs", "vbscript", "workflow", "ws", "wsf",
}


def _safe_name(raw: str) -> str:
    candidate = unquote(raw or "").replace("\\", "/")
    candidate = PurePath(candidate).name
    candidate = "".join(
        ch for ch in candidate if ch >= " " and ch not in {"\x7f", "\r", "\n"}
    ).strip()
    return (candidate or "upload.dat")[:255]


def _safe_upload_extension(name: str, media_type: str) -> str:
    mapped = _MIME_EXTENSIONS.get(media_type)
    if mapped:
        return mapped

    suffix = PurePath(name).suffix.lower().lstrip(".")
    if not suffix:
        return "dat"
    if not _SAFE_EXTENSION_RE.fullmatch(suffix):
        raise HTTPException(
            status_code=415,
            detail={"code": "UNSUPPORTED_FILE_EXTENSION"},
        )
    if suffix in _BLOCKED_RAW_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail={
                "code": "BLOCKED_FILE_EXTENSION",
                "extension": suffix,
            },
        )
    return suffix


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

        name = _safe_name(request.headers.get("x-file-name", "upload.dat"))
        media_type = (
            request.headers.get("content-type", "application/octet-stream")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if not media_type or len(media_type) > 160:
            media_type = "application/octet-stream"
        extension = _safe_upload_extension(name, media_type)

        fd, tmp_name = tempfile.mkstemp(prefix="amodex-upload-", suffix=f".{extension}")
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

            stored = put_media_file(
                media_store,
                Path(tmp_name),
                extension=extension,
            )
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
