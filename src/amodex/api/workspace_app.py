"""Preferred web-first Amodex application composition.

The existing Control Plane app remains the authority owner. This module only layers
workspace routes and replaces the default root projection with the chat workspace.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from amodex.api.app import create_app as create_control_plane_app
from amodex.api.web import DASHBOARD_HTML
from amodex.api.workspace_routes import build_workspace_router
from amodex.api.workspace_web import WORKSPACE_HTML
from amodex.storage.workspace import PostgresWorkspaceRepository

_RELEASE = "0.3-web-workspace"


def create_workspace_app(
    *,
    base_app: FastAPI | None = None,
    workspace_repository: PostgresWorkspaceRepository | None = None,
    dsn: str | None = None,
) -> FastAPI:
    """Compose the web-first surface without creating a second control plane."""
    if base_app is None:
        base_app = create_control_plane_app()
    if workspace_repository is None:
        database_url = dsn or os.environ.get("AMODEX_DATABASE_URL", "")
        if not database_url:
            raise ValueError("workspace_repository, dsn or AMODEX_DATABASE_URL is required")
        workspace_repository = PostgresWorkspaceRepository(database_url)

    # The legacy control-plane root is a projection only, so replacing it does not
    # move authority. All pre-existing API routes remain on the same FastAPI object.
    base_app.router.routes = [
        route
        for route in base_app.router.routes
        if not (
            getattr(route, "path", None) == "/"
            and "GET" in (getattr(route, "methods", None) or set())
        )
    ]
    base_app.include_router(build_workspace_router(workspace_repository))

    @base_app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def workspace_page() -> str:
        return WORKSPACE_HTML

    @base_app.get("/legacy", response_class=HTMLResponse, include_in_schema=False)
    async def legacy_dashboard() -> str:
        return DASHBOARD_HTML

    base_app.title = "Amodex Web Workspace"
    base_app.version = _RELEASE
    return base_app


if os.environ.get("AMODEX_DATABASE_URL"):
    app = create_workspace_app()
else:
    app = FastAPI(title="Amodex Web Workspace", version=_RELEASE)
