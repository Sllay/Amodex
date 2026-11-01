"""Preferred web-first Amodex application composition.

The existing Control Plane app remains the authority owner. This module layers
workspace/connectors routes and replaces only the default root projection.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from amodex.api.app import create_app as create_control_plane_app
from amodex.api.connector_routes import build_connector_router
from amodex.api.connector_web import CONNECTOR_HTML
from amodex.api.web import DASHBOARD_HTML
from amodex.api.workspace_routes import build_workspace_router
from amodex.api.workspace_web import WORKSPACE_HTML
from amodex.connectors.clients import GitHubAppConnector, RobloxOAuthConnector
from amodex.connectors.oauth import GitHubAppConfig, RobloxOAuthConfig
from amodex.connectors.vault import CredentialVault, vault_from_environment
from amodex.storage.connectors import PostgresConnectorRepository
from amodex.storage.workspace import PostgresWorkspaceRepository

_RELEASE = "0.4-web-connectors"


def create_workspace_app(
    *,
    base_app: FastAPI | None = None,
    workspace_repository: PostgresWorkspaceRepository | None = None,
    connector_repository: PostgresConnectorRepository | None = None,
    connector_vault: CredentialVault | None = None,
    github_connector: GitHubAppConnector | None = None,
    roblox_connector: RobloxOAuthConnector | None = None,
    dsn: str | None = None,
) -> FastAPI:
    """Compose the web-first surface without creating a second control plane."""
    if base_app is None:
        base_app = create_control_plane_app()

    database_url = dsn or os.environ.get("AMODEX_DATABASE_URL", "")
    if workspace_repository is None:
        if not database_url:
            raise ValueError("workspace_repository, dsn or AMODEX_DATABASE_URL is required")
        workspace_repository = PostgresWorkspaceRepository(database_url)
    if connector_repository is None:
        if not database_url:
            database_url = workspace_repository.dsn
        connector_repository = PostgresConnectorRepository(database_url)
    connector_vault = connector_vault or vault_from_environment()
    github_connector = github_connector or GitHubAppConnector(
        config=GitHubAppConfig.from_environment(),
        repository=connector_repository,
        vault=connector_vault,
    )
    roblox_connector = roblox_connector or RobloxOAuthConnector(
        config=RobloxOAuthConfig.from_environment(),
        repository=connector_repository,
        vault=connector_vault,
    )

    # The legacy control-plane root is a projection only. Every pre-existing API
    # authority remains on this same FastAPI object.
    base_app.router.routes = [
        route
        for route in base_app.router.routes
        if not (
            getattr(route, "path", None) == "/"
            and "GET" in (getattr(route, "methods", None) or set())
        )
    ]
    base_app.include_router(build_workspace_router(workspace_repository))
    base_app.include_router(
        build_connector_router(
            repository=connector_repository,
            workspace_repository=workspace_repository,
            github=github_connector,
            roblox=roblox_connector,
            vault=connector_vault,
        )
    )

    @base_app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def workspace_page() -> str:
        # Keep Step 1 HTML immutable while exposing Step 2 as a visible product action.
        marker = '<div class="section"><h3>Sistema</h3>'
        connector_action = (
            '<div class="section"><h3>Connections</h3>'
            '<button class="item" onclick="location.href=\'/connections\'">GitHub / Roblox Resources</button></div>'
        )
        return WORKSPACE_HTML.replace(marker, connector_action + marker)

    @base_app.get("/connections", response_class=HTMLResponse, include_in_schema=False)
    async def connector_page() -> str:
        return CONNECTOR_HTML

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
