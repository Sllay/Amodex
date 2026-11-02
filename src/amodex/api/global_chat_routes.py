"""Global-chat projection over the existing durable workspace tables.

Chats remain scoped to a workspace in PostgreSQL. For the web product we reserve
one hidden workspace as the container for chats that are not attached to a user
project. This keeps the storage contract stable while presenting the hierarchy
users expect: global chats in the sidebar, project chats inside each project.
"""

from __future__ import annotations

from threading import Lock

from fastapi import APIRouter, HTTPException, Query

from amodex.storage.workspace import PostgresWorkspaceRepository, WorkspaceStateError
from amodex.workspace.models import ChatCreateRequest, ChatRecord, WorkspaceRecord

GLOBAL_WORKSPACE_NAME = "Amodex Global Chats"
GLOBAL_WORKSPACE_SENTINEL = "__amodex_global_chat_container_v1__"


def build_global_chat_router(repository: PostgresWorkspaceRepository) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["global-chat"])
    create_lock = Lock()
    cached_workspace: WorkspaceRecord | None = None

    def ready() -> PostgresWorkspaceRepository:
        try:
            if not repository.schema_ready():
                raise HTTPException(status_code=503, detail="workspace schema is not ready")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="workspace database is unavailable") from exc
        return repository

    def global_workspace() -> WorkspaceRecord:
        nonlocal cached_workspace
        if cached_workspace is not None:
            return cached_workspace
        repo = ready()
        for item in repo.list_workspaces(limit=500):
            if item.description == GLOBAL_WORKSPACE_SENTINEL:
                cached_workspace = item
                return item
        with create_lock:
            if cached_workspace is not None:
                return cached_workspace
            for item in repo.list_workspaces(limit=500):
                if item.description == GLOBAL_WORKSPACE_SENTINEL:
                    cached_workspace = item
                    return item
            cached_workspace = repo.create_workspace(
                name=GLOBAL_WORKSPACE_NAME,
                description=GLOBAL_WORKSPACE_SENTINEL,
            )
            return cached_workspace

    @router.get("/global-workspace", response_model=WorkspaceRecord)
    def get_global_workspace() -> WorkspaceRecord:
        return global_workspace()

    @router.get("/global-chats", response_model=list[ChatRecord])
    def list_global_chats(
        limit: int = Query(default=300, ge=1, le=500),
    ) -> list[ChatRecord]:
        workspace = global_workspace()
        return list(ready().list_chats(workspace_id=workspace.workspace_id, limit=limit))

    @router.post("/global-chats", response_model=ChatRecord, status_code=201)
    def create_global_chat(body: ChatCreateRequest) -> ChatRecord:
        workspace = global_workspace()
        try:
            return ready().create_chat(
                workspace_id=workspace.workspace_id,
                title=body.title,
                active_environment_id=body.active_environment_id,
            )
        except (KeyError, WorkspaceStateError) as exc:
            code = getattr(exc, "code", "GLOBAL_CHAT_CREATE_FAILED")
            detail = getattr(exc, "detail", str(exc))
            raise HTTPException(
                status_code=409,
                detail={"code": code, "message": detail},
            ) from exc

    return router
