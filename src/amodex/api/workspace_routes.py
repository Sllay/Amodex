"""Web-workspace routes layered over the existing Control Plane API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from amodex.storage.workspace import PostgresWorkspaceRepository, WorkspaceStateError
from amodex.workspace.models import (
    AttachmentCreateRequest,
    AttachmentRecord,
    ChatCreateRequest,
    ChatMessageCreateRequest,
    ChatMessageRecord,
    ChatRecord,
    ChatRole,
    ExecutionDraftCreateRequest,
    ExecutionDraftRecord,
    ExecutionDraftStateRequest,
    WorkspaceCreateRequest,
    WorkspaceEnvironmentBindRequest,
    WorkspaceEnvironmentRecord,
    WorkspaceMemoryCreateRequest,
    WorkspaceMemoryDecisionRequest,
    WorkspaceMemoryRecord,
    WorkspaceRecord,
)


def build_workspace_router(repository: PostgresWorkspaceRepository) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["workspace"])

    def ready() -> PostgresWorkspaceRepository:
        try:
            if not repository.schema_ready():
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "WORKSPACE_SCHEMA_NOT_READY",
                        "message": "web workspace schema has not been applied",
                        "recovery": "apply migrations/postbaseline/0003_web_workspace.sql",
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "WORKSPACE_DATABASE_UNAVAILABLE",
                    "message": "workspace database is unavailable",
                },
            ) from exc
        return repository

    def convert_error(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail="workspace resource not found")
        if isinstance(exc, WorkspaceStateError):
            return HTTPException(status_code=409, detail={"code": exc.code, "message": exc.detail})
        return HTTPException(status_code=409, detail=str(exc))

    @router.get("/workspace/status")
    def workspace_status() -> dict[str, object]:
        ready()
        return {
            "ready": True,
            "workspace_schema_ready": True,
            "chat_inference": "GATED_BENCHMARK_PENDING",
            "binary_upload": "DEFERRED_STEP_3",
            "execution_authority": "ENVIRONMENT_ONLY",
        }

    @router.post("/workspaces", response_model=WorkspaceRecord, status_code=201)
    def create_workspace(body: WorkspaceCreateRequest) -> WorkspaceRecord:
        try:
            return ready().create_workspace(name=body.name, description=body.description)
        except (KeyError, WorkspaceStateError) as exc:
            raise convert_error(exc) from exc

    @router.get("/workspaces", response_model=list[WorkspaceRecord])
    def list_workspaces(limit: int = Query(default=200, ge=1, le=500)) -> list[WorkspaceRecord]:
        return list(ready().list_workspaces(limit=limit))

    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceRecord)
    def get_workspace(workspace_id: str) -> WorkspaceRecord:
        item = ready().get_workspace(workspace_id)
        if item is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return item

    @router.post(
        "/workspaces/{workspace_id}/environments",
        response_model=WorkspaceEnvironmentRecord,
        status_code=201,
    )
    def bind_workspace_environment(
        workspace_id: str,
        body: WorkspaceEnvironmentBindRequest,
    ) -> WorkspaceEnvironmentRecord:
        try:
            return ready().bind_environment(
                workspace_id=workspace_id,
                environment_id=body.environment_id,
                label=body.label,
            )
        except (KeyError, WorkspaceStateError) as exc:
            raise convert_error(exc) from exc

    @router.get(
        "/workspaces/{workspace_id}/environments",
        response_model=list[WorkspaceEnvironmentRecord],
    )
    def list_workspace_environments(workspace_id: str) -> list[WorkspaceEnvironmentRecord]:
        if ready().get_workspace(workspace_id) is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return list(ready().list_environments(workspace_id=workspace_id))

    @router.post(
        "/workspaces/{workspace_id}/chats",
        response_model=ChatRecord,
        status_code=201,
    )
    def create_chat(workspace_id: str, body: ChatCreateRequest) -> ChatRecord:
        try:
            return ready().create_chat(
                workspace_id=workspace_id,
                title=body.title,
                active_environment_id=body.active_environment_id,
            )
        except (KeyError, WorkspaceStateError) as exc:
            raise convert_error(exc) from exc

    @router.get("/workspaces/{workspace_id}/chats", response_model=list[ChatRecord])
    def list_chats(
        workspace_id: str,
        limit: int = Query(default=300, ge=1, le=500),
    ) -> list[ChatRecord]:
        if ready().get_workspace(workspace_id) is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return list(ready().list_chats(workspace_id=workspace_id, limit=limit))

    @router.get("/chats/{chat_id}", response_model=ChatRecord)
    def get_chat(chat_id: str) -> ChatRecord:
        item = ready().get_chat(chat_id)
        if item is None:
            raise HTTPException(status_code=404, detail="chat not found")
        return item

    @router.post(
        "/chats/{chat_id}/messages",
        response_model=ChatMessageRecord,
        status_code=201,
    )
    def add_owner_message(
        chat_id: str,
        body: ChatMessageCreateRequest,
    ) -> ChatMessageRecord:
        if body.role != ChatRole.OWNER:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "CHAT_ROLE_NOT_PUBLICLY_WRITABLE",
                    "message": "public chat clients may only write OWNER messages",
                },
            )
        try:
            return ready().add_message(chat_id=chat_id, role=ChatRole.OWNER, content=body.content)
        except (KeyError, WorkspaceStateError) as exc:
            raise convert_error(exc) from exc

    @router.get("/chats/{chat_id}/messages", response_model=list[ChatMessageRecord])
    def list_messages(
        chat_id: str,
        limit: int = Query(default=500, ge=1, le=1000),
    ) -> list[ChatMessageRecord]:
        if ready().get_chat(chat_id) is None:
            raise HTTPException(status_code=404, detail="chat not found")
        return list(ready().list_messages(chat_id=chat_id, limit=limit))

    @router.post(
        "/workspaces/{workspace_id}/memories",
        response_model=WorkspaceMemoryRecord,
        status_code=201,
    )
    def create_memory(
        workspace_id: str,
        body: WorkspaceMemoryCreateRequest,
    ) -> WorkspaceMemoryRecord:
        if ready().get_workspace(workspace_id) is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return ready().create_memory(
            workspace_id=workspace_id,
            content=body.content,
            source_ref=body.source_ref,
        )

    @router.get(
        "/workspaces/{workspace_id}/memories",
        response_model=list[WorkspaceMemoryRecord],
    )
    def list_memories(
        workspace_id: str,
        include_rejected: bool = False,
    ) -> list[WorkspaceMemoryRecord]:
        if ready().get_workspace(workspace_id) is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return list(
            ready().list_memories(
                workspace_id=workspace_id,
                include_rejected=include_rejected,
            )
        )

    @router.post("/memories/{memory_id}/decision", response_model=WorkspaceMemoryRecord)
    def decide_memory(
        memory_id: str,
        body: WorkspaceMemoryDecisionRequest,
    ) -> WorkspaceMemoryRecord:
        try:
            return ready().set_memory_status(memory_id=memory_id, status=body.status)
        except (KeyError, WorkspaceStateError) as exc:
            raise convert_error(exc) from exc

    @router.post(
        "/chats/{chat_id}/attachments",
        response_model=AttachmentRecord,
        status_code=201,
    )
    def create_attachment_metadata(
        chat_id: str,
        body: AttachmentCreateRequest,
    ) -> AttachmentRecord:
        try:
            return ready().create_attachment(
                chat_id=chat_id,
                name=body.name,
                media_type=body.media_type,
                size_bytes=body.size_bytes,
                sha256=body.sha256,
                storage_ref=body.storage_ref,
            )
        except (KeyError, WorkspaceStateError) as exc:
            raise convert_error(exc) from exc

    @router.get("/chats/{chat_id}/attachments", response_model=list[AttachmentRecord])
    def list_attachments(chat_id: str) -> list[AttachmentRecord]:
        if ready().get_chat(chat_id) is None:
            raise HTTPException(status_code=404, detail="chat not found")
        return list(ready().list_attachments(chat_id=chat_id))

    @router.post(
        "/chats/{chat_id}/drafts",
        response_model=ExecutionDraftRecord,
        status_code=201,
    )
    def create_execution_draft(
        chat_id: str,
        body: ExecutionDraftCreateRequest,
    ) -> ExecutionDraftRecord:
        try:
            return ready().create_draft(
                chat_id=chat_id,
                environment_id=body.environment_id,
                title=body.title,
                objective=body.objective,
                instructions=body.instructions,
                validation=body.validation,
            )
        except (KeyError, WorkspaceStateError) as exc:
            raise convert_error(exc) from exc

    @router.get("/chats/{chat_id}/drafts", response_model=list[ExecutionDraftRecord])
    def list_execution_drafts(chat_id: str) -> list[ExecutionDraftRecord]:
        if ready().get_chat(chat_id) is None:
            raise HTTPException(status_code=404, detail="chat not found")
        return list(ready().list_drafts(chat_id=chat_id))

    @router.post("/drafts/{draft_id}/state", response_model=ExecutionDraftRecord)
    def set_execution_draft_state(
        draft_id: str,
        body: ExecutionDraftStateRequest,
    ) -> ExecutionDraftRecord:
        try:
            return ready().set_draft_status(draft_id=draft_id, status=body.status)
        except (KeyError, WorkspaceStateError) as exc:
            raise convert_error(exc) from exc

    return router
