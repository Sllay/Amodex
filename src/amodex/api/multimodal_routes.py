"""Explicit Step 3 media/search routes on the existing Control Plane app."""

from __future__ import annotations

from pathlib import PurePath
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request, Response

from amodex.multimodal.clients import MultimodalClientError, MultimodalClients
from amodex.multimodal.models import (
    MessageAttachmentLinkRequest,
    MessageAttachmentRecord,
    MultimodalOperationKind,
    MultimodalOperationRecord,
    MultimodalOperationStatus,
    ResearchRequest,
    SpeechRequest,
    TranscriptionRequest,
    VisionRequest,
)
from amodex.multimodal.object_store import (
    MediaObjectMissing,
    MediaObjectStore,
    MediaStoreError,
    MediaStoreUnavailable,
)
from amodex.storage.multimodal import PostgresMultimodalRepository
from amodex.storage.workspace import PostgresWorkspaceRepository, WorkspaceStateError
from amodex.workspace.models import AttachmentRecord, ChatRole


def _safe_name(raw: str) -> str:
    candidate = unquote(raw or "").replace("\\", "/")
    candidate = PurePath(candidate).name
    candidate = "".join(ch for ch in candidate if ch >= " " and ch not in {"\x7f", "\r", "\n"}).strip()
    if not candidate:
        return "upload.bin"
    return candidate[:255]


def _operation_failure_status(code: str) -> MultimodalOperationStatus:
    if "BILLING" in code or "NOT_CONFIGURED" in code or "CONFIGURATION" in code:
        return MultimodalOperationStatus.BLOCKED
    return MultimodalOperationStatus.FAILED


def build_multimodal_router(
    *,
    workspace_repository: PostgresWorkspaceRepository,
    repository: PostgresMultimodalRepository,
    media_store: MediaObjectStore,
    clients: MultimodalClients,
    max_upload_bytes: int,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["multimodal"])

    def ready() -> PostgresMultimodalRepository:
        try:
            if not repository.schema_ready():
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "MULTIMODAL_SCHEMA_NOT_READY",
                        "message": "multimodal chat schema has not been applied",
                        "recovery": "apply migrations/postbaseline/0005_multimodal_chat.sql",
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "MULTIMODAL_DATABASE_UNAVAILABLE", "message": "multimodal database is unavailable"},
            ) from exc
        return repository

    def ensure_chat(chat_id: str) -> None:
        if workspace_repository.get_chat(chat_id) is None:
            raise HTTPException(status_code=404, detail="chat not found")

    def attachment_or_404(attachment_id: str) -> AttachmentRecord:
        item = ready().get_attachment(attachment_id)
        if item is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        return item

    def workspace_error(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail="multimodal resource not found")
        if isinstance(exc, WorkspaceStateError):
            return HTTPException(status_code=409, detail={"code": exc.code, "message": exc.detail})
        return HTTPException(status_code=409, detail=str(exc))

    @router.get("/multimodal/status")
    async def multimodal_status() -> dict[str, object]:
        repo = ready()
        return {
            "ready": True,
            "schema_ready": repo.schema_ready(),
            "binary_upload": "READY" if media_store.configured else "NOT_CONFIGURED",
            "max_upload_bytes": max_upload_bytes,
            "vision": {
                "ready": clients.vision.ready,
                "reason": clients.vision.readiness_reason,
                "provider": clients.vision.provider,
                "model": clients.vision.model,
                "billing_class": clients.vision.billing_class,
            },
            "stt": {
                "ready": clients.stt.ready,
                "reason": clients.stt.readiness_reason,
                "provider": clients.stt.provider,
                "model": clients.stt.model,
                "billing_class": clients.stt.billing_class,
            },
            "server_tts": {
                "ready": clients.tts.ready,
                "reason": clients.tts.readiness_reason,
                "provider": clients.tts.provider,
                "model": clients.tts.model,
                "billing_class": clients.tts.billing_class,
            },
            "browser_tts": "AVAILABLE",
            "web_research": {
                "ready": clients.research.ready,
                "reason": clients.research.readiness_reason,
                "provider": clients.research.provider,
            },
            "automatic_model_routing": "GATED_STEP_4",
            "execution_authority": "ENVIRONMENT_ONLY",
        }

    @router.post("/chats/{chat_id}/uploads", response_model=AttachmentRecord, status_code=201)
    async def upload_attachment(chat_id: str, request: Request) -> AttachmentRecord:
        ready()
        ensure_chat(chat_id)
        if not media_store.configured:
            raise HTTPException(
                status_code=503,
                detail={"code": "MEDIA_STORE_NOT_CONFIGURED", "message": "configure AMODEX_MEDIA_STORE_DIR"},
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
        data = bytearray()
        async for chunk in request.stream():
            if chunk:
                data.extend(chunk)
            if len(data) > max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "UPLOAD_TOO_LARGE", "max_upload_bytes": max_upload_bytes},
                )
        if not data:
            raise HTTPException(status_code=400, detail={"code": "EMPTY_UPLOAD"})
        name = _safe_name(request.headers.get("x-file-name", "upload.bin"))
        media_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip().lower()
        if not media_type or len(media_type) > 160:
            media_type = "application/octet-stream"
        try:
            stored = media_store.put_bytes(bytes(data))
            return workspace_repository.create_attachment(
                chat_id=chat_id,
                name=name,
                media_type=media_type,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                storage_ref=stored.storage_ref,
            )
        except (MediaStoreError, KeyError, WorkspaceStateError) as exc:
            raise workspace_error(exc) from exc

    @router.get("/attachments/{attachment_id}/content")
    async def attachment_content(attachment_id: str) -> Response:
        attachment = attachment_or_404(attachment_id)
        try:
            data = media_store.get_bytes(attachment.storage_ref)
        except MediaStoreUnavailable as exc:
            raise HTTPException(status_code=503, detail={"code": "MEDIA_STORE_NOT_CONFIGURED"}) from exc
        except MediaObjectMissing as exc:
            raise HTTPException(status_code=404, detail={"code": "MEDIA_OBJECT_MISSING"}) from exc
        except MediaStoreError as exc:
            raise HTTPException(status_code=409, detail={"code": "MEDIA_OBJECT_INVALID"}) from exc
        safe = attachment.name.replace('"', "_")
        return Response(
            content=data,
            media_type=attachment.media_type,
            headers={"content-disposition": f'inline; filename="{safe}"', "x-content-type-options": "nosniff"},
        )

    @router.post(
        "/messages/{message_id}/attachments",
        response_model=list[MessageAttachmentRecord],
        status_code=201,
    )
    async def link_message_attachments(
        message_id: str,
        body: MessageAttachmentLinkRequest,
    ) -> list[MessageAttachmentRecord]:
        try:
            return list(ready().link_attachments(message_id=message_id, attachment_ids=body.attachment_ids))
        except (KeyError, WorkspaceStateError) as exc:
            raise workspace_error(exc) from exc

    @router.get("/chats/{chat_id}/attachment-links", response_model=list[MessageAttachmentRecord])
    async def list_attachment_links(chat_id: str) -> list[MessageAttachmentRecord]:
        ensure_chat(chat_id)
        return list(ready().list_attachment_links(chat_id=chat_id))

    @router.get("/chats/{chat_id}/multimodal-operations", response_model=list[MultimodalOperationRecord])
    async def list_multimodal_operations(
        chat_id: str,
        limit: int = Query(default=200, ge=1, le=500),
    ) -> list[MultimodalOperationRecord]:
        ensure_chat(chat_id)
        return list(ready().list_operations(chat_id=chat_id, limit=limit))

    @router.post("/attachments/{attachment_id}/vision", response_model=MultimodalOperationRecord)
    async def analyze_attachment(attachment_id: str, body: VisionRequest) -> MultimodalOperationRecord:
        attachment = attachment_or_404(attachment_id)
        if not attachment.media_type.startswith("image/"):
            raise HTTPException(status_code=415, detail={"code": "VISION_REQUIRES_IMAGE"})
        op = ready().create_operation(
            chat_id=str(attachment.chat_id),
            kind=MultimodalOperationKind.VISION,
            attachment_id=attachment_id,
            provider=clients.vision.provider,
            model=clients.vision.model,
            billing_class=clients.vision.billing_class,
            input_text=body.prompt,
            provenance={"explicit_user_action": True},
        )
        if not clients.vision.ready:
            blocked = ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.BLOCKED,
                error_code=clients.vision.readiness_reason,
            )
            raise HTTPException(
                status_code=409,
                detail={"code": blocked.error_code, "operation_id": str(blocked.operation_id)},
            )
        try:
            data = media_store.get_bytes(attachment.storage_ref)
            result = await clients.vision.analyze(data=data, media_type=attachment.media_type, prompt=body.prompt)
            finished = ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.SUCCEEDED,
                result_text=result.text,
                provenance=result.provenance,
            )
            workspace_repository.add_message(
                chat_id=str(attachment.chat_id),
                role=ChatRole.AMODEX,
                content=f"🔎 Análise visual — {attachment.name}\n{result.text}",
            )
            return finished
        except MultimodalClientError as exc:
            ready().finish_operation(
                operation_id=str(op.operation_id),
                status=_operation_failure_status(exc.code),
                error_code=exc.code,
            )
            raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message}) from exc
        except (MediaStoreError, MediaStoreUnavailable) as exc:
            ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.FAILED,
                error_code="MEDIA_READ_FAILED",
            )
            raise HTTPException(status_code=409, detail={"code": "MEDIA_READ_FAILED"}) from exc

    @router.post("/attachments/{attachment_id}/transcribe", response_model=MultimodalOperationRecord)
    async def transcribe_attachment(
        attachment_id: str,
        body: TranscriptionRequest,
    ) -> MultimodalOperationRecord:
        attachment = attachment_or_404(attachment_id)
        if not attachment.media_type.startswith("audio/"):
            raise HTTPException(status_code=415, detail={"code": "STT_REQUIRES_AUDIO"})
        op = ready().create_operation(
            chat_id=str(attachment.chat_id),
            kind=MultimodalOperationKind.STT,
            attachment_id=attachment_id,
            provider=clients.stt.provider,
            model=clients.stt.model,
            billing_class=clients.stt.billing_class,
            input_text=body.language,
            provenance={"explicit_user_action": True},
        )
        if not clients.stt.ready:
            blocked = ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.BLOCKED,
                error_code=clients.stt.readiness_reason,
            )
            raise HTTPException(status_code=409, detail={"code": blocked.error_code, "operation_id": str(blocked.operation_id)})
        try:
            data = media_store.get_bytes(attachment.storage_ref)
            result = await clients.stt.transcribe(
                data=data,
                name=attachment.name,
                media_type=attachment.media_type,
                language=body.language,
            )
            return ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.SUCCEEDED,
                result_text=result.text,
                provenance=result.provenance,
            )
        except MultimodalClientError as exc:
            ready().finish_operation(
                operation_id=str(op.operation_id),
                status=_operation_failure_status(exc.code),
                error_code=exc.code,
            )
            raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message}) from exc
        except (MediaStoreError, MediaStoreUnavailable) as exc:
            ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.FAILED,
                error_code="MEDIA_READ_FAILED",
            )
            raise HTTPException(status_code=409, detail={"code": "MEDIA_READ_FAILED"}) from exc

    @router.post("/chats/{chat_id}/speech", response_model=MultimodalOperationRecord)
    async def synthesize_speech(chat_id: str, body: SpeechRequest) -> MultimodalOperationRecord:
        ensure_chat(chat_id)
        op = ready().create_operation(
            chat_id=chat_id,
            kind=MultimodalOperationKind.TTS,
            provider=clients.tts.provider,
            model=clients.tts.model,
            billing_class=clients.tts.billing_class,
            input_text=body.text,
            provenance={"explicit_user_action": True},
        )
        if not clients.tts.ready:
            blocked = ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.BLOCKED,
                error_code=clients.tts.readiness_reason,
            )
            raise HTTPException(status_code=409, detail={"code": blocked.error_code, "operation_id": str(blocked.operation_id)})
        try:
            result = await clients.tts.synthesize(text=body.text, voice=body.voice)
            stored = media_store.put_bytes(result.data)
            attachment = workspace_repository.create_attachment(
                chat_id=chat_id,
                name=f"speech-{op.operation_id}.{result.extension}",
                media_type=result.media_type,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                storage_ref=stored.storage_ref,
            )
            return ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.SUCCEEDED,
                output_attachment_id=str(attachment.attachment_id),
                provenance=result.provenance,
            )
        except MultimodalClientError as exc:
            ready().finish_operation(
                operation_id=str(op.operation_id),
                status=_operation_failure_status(exc.code),
                error_code=exc.code,
            )
            raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message}) from exc
        except MediaStoreError as exc:
            ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.FAILED,
                error_code="MEDIA_WRITE_FAILED",
            )
            raise HTTPException(status_code=409, detail={"code": "MEDIA_WRITE_FAILED"}) from exc

    @router.post("/chats/{chat_id}/research", response_model=MultimodalOperationRecord)
    async def research_chat(chat_id: str, body: ResearchRequest) -> MultimodalOperationRecord:
        ensure_chat(chat_id)
        op = ready().create_operation(
            chat_id=chat_id,
            kind=MultimodalOperationKind.WEB_RESEARCH,
            provider=clients.research.provider,
            input_text=body.query,
            provenance={"explicit_user_action": True, "page_fetch": False},
        )
        if not clients.research.ready:
            blocked = ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.BLOCKED,
                error_code=clients.research.readiness_reason,
            )
            raise HTTPException(status_code=409, detail={"code": blocked.error_code, "operation_id": str(blocked.operation_id)})
        try:
            result = await clients.research.search(
                query=body.query,
                max_results=body.max_results,
                language=body.language,
            )
            if result.sources:
                lines = [f"🌐 Pesquisa — {body.query}"]
                for index, source in enumerate(result.sources, 1):
                    snippet = f" — {source.snippet}" if source.snippet else ""
                    lines.append(f"{index}. {source.title}{snippet}\n{source.url}")
                text = "\n\n".join(lines)
            else:
                text = f"🌐 Pesquisa — {body.query}\nNenhum resultado retornado pelo mecanismo configurado."
            provenance = dict(result.provenance)
            provenance["sources"] = [source.model_dump(mode="json") for source in result.sources]
            finished = ready().finish_operation(
                operation_id=str(op.operation_id),
                status=MultimodalOperationStatus.SUCCEEDED,
                result_text=text,
                provenance=provenance,
            )
            workspace_repository.add_message(chat_id=chat_id, role=ChatRole.AMODEX, content=text)
            return finished
        except MultimodalClientError as exc:
            ready().finish_operation(
                operation_id=str(op.operation_id),
                status=_operation_failure_status(exc.code),
                error_code=exc.code,
            )
            raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message}) from exc

    return router
