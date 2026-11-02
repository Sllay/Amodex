"""Fast, batched UI projections for the Amodex web product.

These routes intentionally use regular synchronous FastAPI handlers. The underlying
PostgreSQL repositories use synchronous psycopg, so FastAPI executes these handlers
in its worker thread pool instead of blocking the single Uvicorn event loop.

The browser-facing snapshots also collapse several Neon round trips into one HTTP
request and, where useful, one PostgreSQL connection.
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg.rows import dict_row

from amodex.connectors.clients import GitHubAppConnector, RobloxOAuthConnector
from amodex.connectors.vault import CredentialVault
from amodex.contracts.ids import new_uuid7
from amodex.storage.connectors import PostgresConnectorRepository
from amodex.storage.workspace import PostgresWorkspaceRepository, WorkspaceStateError
from amodex.workspace.models import ChatCreateRequest, ChatMessageCreateRequest, ChatRole, WorkspaceCreateRequest

GLOBAL_WORKSPACE_NAME = "Amodex Global Chats"
GLOBAL_WORKSPACE_SENTINEL = "__amodex_global_chat_container_v1__"
_GLOBAL_LOCK_NAME = "amodex-global-workspace-v1"


def build_workspace_ui_router(
    *,
    workspace_repository: PostgresWorkspaceRepository,
    connector_repository: PostgresConnectorRepository,
    github: GitHubAppConnector,
    roblox: RobloxOAuthConnector,
    vault: CredentialVault,
) -> APIRouter:
    router = APIRouter(prefix="/v1/ui", tags=["web-ui"])
    dsn = workspace_repository.dsn

    def fail(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail="workspace resource not found")
        if isinstance(exc, WorkspaceStateError):
            return HTTPException(status_code=409, detail={"code": exc.code, "message": exc.detail})
        if isinstance(exc, psycopg.Error):
            return HTTPException(
                status_code=503,
                detail={"code": "UI_DATABASE_UNAVAILABLE", "message": "Amodex database is temporarily unavailable"},
            )
        return HTTPException(status_code=409, detail=str(exc))

    def ensure_global_workspace(conn: psycopg.Connection[Any]) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM amodex_workspace_projects WHERE description=%s ORDER BY created_at LIMIT 1",
            (GLOBAL_WORKSPACE_SENTINEL,),
        ).fetchone()
        if row is not None:
            return row

        # Cross-thread/process safe creation without a schema change.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_GLOBAL_LOCK_NAME,))
        row = conn.execute(
            "SELECT * FROM amodex_workspace_projects WHERE description=%s ORDER BY created_at LIMIT 1",
            (GLOBAL_WORKSPACE_SENTINEL,),
        ).fetchone()
        if row is not None:
            return row

        row = conn.execute(
            """
            INSERT INTO amodex_workspace_projects(
                workspace_id,name,description,status,created_at,updated_at
            ) VALUES (%s,%s,%s,'ACTIVE',NOW(),NOW()) RETURNING *
            """,
            (new_uuid7(), GLOBAL_WORKSPACE_NAME, GLOBAL_WORKSPACE_SENTINEL),
        ).fetchone()
        assert row is not None
        return row

    @router.get("/bootstrap")
    def bootstrap() -> dict[str, Any]:
        try:
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                global_workspace = ensure_global_workspace(conn)
                projects = conn.execute(
                    """
                    SELECT * FROM amodex_workspace_projects
                    WHERE description IS DISTINCT FROM %s AND status='ACTIVE'
                    ORDER BY updated_at DESC LIMIT 200
                    """,
                    (GLOBAL_WORKSPACE_SENTINEL,),
                ).fetchall()
                global_chats = conn.execute(
                    """
                    SELECT * FROM amodex_workspace_chats
                    WHERE workspace_id=%s AND status='ACTIVE'
                    ORDER BY updated_at DESC LIMIT 300
                    """,
                    (global_workspace["workspace_id"],),
                ).fetchall()
            return {
                "ready": True,
                "chat_inference": "GATED_BENCHMARK_PENDING",
                "projects": projects,
                "global_workspace": global_workspace,
                "global_chats": global_chats,
            }
        except Exception as exc:
            raise fail(exc) from exc

    @router.get("/projects/{workspace_id}")
    def project_snapshot(workspace_id: str) -> dict[str, Any]:
        try:
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                project = conn.execute(
                    "SELECT * FROM amodex_workspace_projects WHERE workspace_id=%s AND description IS DISTINCT FROM %s",
                    (workspace_id, GLOBAL_WORKSPACE_SENTINEL),
                ).fetchone()
                if project is None:
                    raise KeyError(workspace_id)
                chats = conn.execute(
                    """
                    SELECT * FROM amodex_workspace_chats
                    WHERE workspace_id=%s AND status='ACTIVE'
                    ORDER BY updated_at DESC LIMIT 300
                    """,
                    (workspace_id,),
                ).fetchall()
                memories = conn.execute(
                    """
                    SELECT * FROM amodex_workspace_memories
                    WHERE workspace_id=%s AND status<>'REJECTED'
                    ORDER BY updated_at DESC
                    """,
                    (workspace_id,),
                ).fetchall()
                bindings = conn.execute(
                    """
                    SELECT * FROM amodex_workspace_environments
                    WHERE workspace_id=%s ORDER BY created_at
                    """,
                    (workspace_id,),
                ).fetchall()
                environments = conn.execute(
                    """
                    SELECT environment_id,name,engine,status,created_at,updated_at
                    FROM amodex_environments ORDER BY created_at,environment_id
                    """
                ).fetchall()
            return {
                "project": project,
                "chats": chats,
                "memories": memories,
                "bindings": bindings,
                "environments": environments,
            }
        except Exception as exc:
            raise fail(exc) from exc

    @router.get("/chats/{chat_id}")
    def chat_snapshot(chat_id: str) -> dict[str, Any]:
        try:
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                chat = conn.execute(
                    "SELECT * FROM amodex_workspace_chats WHERE chat_id=%s AND status='ACTIVE'",
                    (chat_id,),
                ).fetchone()
                if chat is None:
                    raise KeyError(chat_id)
                workspace = conn.execute(
                    "SELECT * FROM amodex_workspace_projects WHERE workspace_id=%s",
                    (chat["workspace_id"],),
                ).fetchone()
                if workspace is None:
                    raise KeyError(str(chat["workspace_id"]))
                messages = conn.execute(
                    """
                    SELECT * FROM amodex_workspace_messages
                    WHERE chat_id=%s ORDER BY created_at ASC LIMIT 500
                    """,
                    (chat_id,),
                ).fetchall()
                drafts = conn.execute(
                    """
                    SELECT * FROM amodex_execution_drafts
                    WHERE chat_id=%s ORDER BY updated_at DESC
                    """,
                    (chat_id,),
                ).fetchall()
                memories = conn.execute(
                    """
                    SELECT * FROM amodex_workspace_memories
                    WHERE workspace_id=%s AND status<>'REJECTED'
                    ORDER BY updated_at DESC
                    """,
                    (chat["workspace_id"],),
                ).fetchall()
                bindings = conn.execute(
                    """
                    SELECT * FROM amodex_workspace_environments
                    WHERE workspace_id=%s ORDER BY created_at
                    """,
                    (chat["workspace_id"],),
                ).fetchall()
                environments = conn.execute(
                    """
                    SELECT environment_id,name,engine,status,created_at,updated_at
                    FROM amodex_environments ORDER BY created_at,environment_id
                    """
                ).fetchall()
            return {
                "chat": chat,
                "workspace": workspace,
                "messages": messages,
                "drafts": drafts,
                "memories": memories,
                "bindings": bindings,
                "environments": environments,
            }
        except Exception as exc:
            raise fail(exc) from exc

    @router.post("/projects", status_code=201)
    def create_project(body: WorkspaceCreateRequest):
        try:
            return workspace_repository.create_workspace(name=body.name, description=body.description)
        except Exception as exc:
            raise fail(exc) from exc

    @router.post("/projects/{workspace_id}/chats", status_code=201)
    def create_project_chat(workspace_id: str, body: ChatCreateRequest):
        try:
            return workspace_repository.create_chat(
                workspace_id=workspace_id,
                title=body.title,
                active_environment_id=body.active_environment_id,
            )
        except Exception as exc:
            raise fail(exc) from exc

    @router.post("/global-chats", status_code=201)
    def create_global_chat(body: ChatCreateRequest):
        try:
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                global_workspace = ensure_global_workspace(conn)
                workspace_id = str(global_workspace["workspace_id"])
            return workspace_repository.create_chat(
                workspace_id=workspace_id,
                title=body.title,
                active_environment_id=None,
            )
        except Exception as exc:
            raise fail(exc) from exc

    @router.post("/chats/{chat_id}/messages", status_code=201)
    def add_owner_message(chat_id: str, body: ChatMessageCreateRequest):
        if body.role != ChatRole.OWNER:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "CHAT_ROLE_NOT_PUBLICLY_WRITABLE",
                    "message": "public chat clients may only write OWNER messages",
                },
            )
        try:
            return workspace_repository.add_message(chat_id=chat_id, role=ChatRole.OWNER, content=body.content)
        except Exception as exc:
            raise fail(exc) from exc

    @router.get("/connections/bootstrap")
    def connections_bootstrap() -> dict[str, Any]:
        try:
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                projects = conn.execute(
                    """
                    SELECT workspace_id,name,description,status,created_at,updated_at
                    FROM amodex_workspace_projects
                    WHERE description IS DISTINCT FROM %s AND status='ACTIVE'
                    ORDER BY updated_at DESC LIMIT 200
                    """,
                    (GLOBAL_WORKSPACE_SENTINEL,),
                ).fetchall()
            statuses = [
                {
                    "provider": "GITHUB",
                    "configured": github.config is not None,
                    "vault_available": vault.available,
                    "state": "READY" if github.config is not None and vault.available else "NOT_CONFIGURED",
                },
                {
                    "provider": "ROBLOX",
                    "configured": roblox.config is not None,
                    "vault_available": vault.available,
                    "state": "READY" if roblox.config is not None and vault.available else "NOT_CONFIGURED",
                },
            ]
            return {"projects": projects, "statuses": statuses}
        except Exception as exc:
            raise fail(exc) from exc

    @router.get("/connections/projects/{workspace_id}")
    def connection_project_snapshot(workspace_id: str) -> dict[str, Any]:
        try:
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                project = conn.execute(
                    "SELECT workspace_id,name FROM amodex_workspace_projects WHERE workspace_id=%s",
                    (workspace_id,),
                ).fetchone()
                if project is None:
                    raise KeyError(workspace_id)
                bindings = conn.execute(
                    """
                    SELECT workspace_id,environment_id,label,created_at
                    FROM amodex_workspace_environments
                    WHERE workspace_id=%s ORDER BY created_at
                    """,
                    (workspace_id,),
                ).fetchall()
                accounts = conn.execute(
                    """
                    SELECT connector_id,workspace_id,provider,external_account_id,external_account_name,
                           state,scopes,metadata,created_at,updated_at
                    FROM amodex_connector_accounts
                    WHERE workspace_id=%s ORDER BY updated_at DESC
                    """,
                    (workspace_id,),
                ).fetchall()
                resources = conn.execute(
                    """
                    SELECT r.resource_id,r.connector_id,r.resource_type,r.external_id,r.name,r.url,
                           r.capabilities,r.metadata,r.bound_environment_id,r.created_at,r.updated_at
                    FROM amodex_connector_resources r
                    JOIN amodex_connector_accounts a ON a.connector_id=r.connector_id
                    WHERE a.workspace_id=%s
                    ORDER BY r.resource_type,r.name
                    """,
                    (workspace_id,),
                ).fetchall()
            return {"project": project, "bindings": bindings, "accounts": accounts, "resources": resources}
        except Exception as exc:
            raise fail(exc) from exc

    return router
