"""Web connector routes for GitHub App and Roblox Open Cloud OAuth."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from amodex.connectors.clients import (
    ConnectorConfigurationError,
    GitHubAppConnector,
    RobloxOAuthConnector,
)
from amodex.connectors.models import (
    ConnectorAccountRecord,
    ConnectorProvider,
    ConnectorRefreshResponse,
    ConnectorResourceBindRequest,
    ConnectorResourceRecord,
    ConnectorStatusRecord,
)
from amodex.connectors.vault import CredentialVault
from amodex.storage.connectors import ConnectorStateError, PostgresConnectorRepository
from amodex.storage.workspace import PostgresWorkspaceRepository


class RobloxPlaceAddRequest(BaseModel):
    universe_id: str = Field(min_length=1, max_length=64, pattern=r"^[0-9]+$")
    place_id: str = Field(min_length=1, max_length=64, pattern=r"^[0-9]+$")


def build_connector_router(
    *,
    repository: PostgresConnectorRepository,
    workspace_repository: PostgresWorkspaceRepository,
    github: GitHubAppConnector,
    roblox: RobloxOAuthConnector,
    vault: CredentialVault,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["connectors"])

    def ready() -> PostgresConnectorRepository:
        try:
            if not repository.schema_ready():
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "CONNECTOR_SCHEMA_NOT_READY",
                        "message": "connector schema has not been applied",
                        "recovery": "apply migrations/postbaseline/0004_connectors.sql",
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "CONNECTOR_DATABASE_UNAVAILABLE", "message": "connector database unavailable"},
            ) from exc
        return repository

    def convert(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail="connector resource not found")
        if isinstance(exc, ConnectorConfigurationError):
            return HTTPException(
                status_code=503,
                detail={"code": "CONNECTOR_NOT_CONFIGURED", "message": str(exc)},
            )
        if isinstance(exc, ConnectorStateError):
            return HTTPException(status_code=409, detail={"code": exc.code, "message": exc.detail})
        return HTTPException(status_code=502, detail=str(exc))

    @router.get("/connectors/status", response_model=list[ConnectorStatusRecord])
    def connector_status() -> list[ConnectorStatusRecord]:
        ready()
        return [
            ConnectorStatusRecord(
                provider=ConnectorProvider.GITHUB,
                configured=github.config is not None,
                vault_available=vault.available,
                state="READY" if github.config is not None and vault.available else "NOT_CONFIGURED",
                detail=None if github.config is not None else "GitHub App environment configuration missing",
            ),
            ConnectorStatusRecord(
                provider=ConnectorProvider.ROBLOX,
                configured=roblox.config is not None,
                vault_available=vault.available,
                state="READY" if roblox.config is not None and vault.available else "NOT_CONFIGURED",
                detail=None if roblox.config is not None else "Roblox OAuth environment configuration missing",
            ),
        ]

    @router.get(
        "/workspaces/{workspace_id}/connectors",
        response_model=list[ConnectorAccountRecord],
    )
    def list_workspace_connectors(workspace_id: str) -> list[ConnectorAccountRecord]:
        ready()
        if workspace_repository.get_workspace(workspace_id) is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return list(repository.list_accounts(workspace_id=workspace_id))

    @router.post("/workspaces/{workspace_id}/connectors/github/start")
    def start_github(workspace_id: str):
        try:
            ready()
            if workspace_repository.get_workspace(workspace_id) is None:
                raise KeyError(workspace_id)
            return github.start(workspace_id=workspace_id)
        except Exception as exc:
            raise convert(exc) from exc

    @router.get("/connectors/github/callback", include_in_schema=True)
    def github_callback(code: str = Query(min_length=1), state: str = Query(min_length=1)):
        try:
            account = github.complete(code=code, state=state)
            return RedirectResponse(url=f"/?connector={account.connector_id}&provider=github", status_code=303)
        except Exception as exc:
            raise convert(exc) from exc

    @router.post("/workspaces/{workspace_id}/connectors/roblox/start")
    def start_roblox(workspace_id: str):
        try:
            ready()
            if workspace_repository.get_workspace(workspace_id) is None:
                raise KeyError(workspace_id)
            return roblox.start(workspace_id=workspace_id)
        except Exception as exc:
            raise convert(exc) from exc

    @router.get("/connectors/roblox/callback", include_in_schema=True)
    def roblox_callback(code: str = Query(min_length=1), state: str = Query(min_length=1)):
        try:
            account = roblox.complete(code=code, state=state)
            return RedirectResponse(url=f"/?connector={account.connector_id}&provider=roblox", status_code=303)
        except Exception as exc:
            raise convert(exc) from exc

    @router.post("/connectors/{connector_id}/refresh", response_model=ConnectorRefreshResponse)
    def refresh_connector(connector_id: str) -> ConnectorRefreshResponse:
        try:
            account = ready().get_account(connector_id)
            if account is None:
                raise KeyError(connector_id)
            if account.provider == ConnectorProvider.GITHUB:
                return github.refresh_resources(connector_id=connector_id)
            return roblox.refresh_resources(connector_id=connector_id)
        except Exception as exc:
            raise convert(exc) from exc

    @router.get("/connectors/{connector_id}/resources", response_model=list[ConnectorResourceRecord])
    def list_connector_resources(connector_id: str) -> list[ConnectorResourceRecord]:
        if ready().get_account(connector_id) is None:
            raise HTTPException(status_code=404, detail="connector not found")
        return list(repository.list_resources(connector_id=connector_id))

    @router.post(
        "/connectors/{connector_id}/roblox/places",
        response_model=ConnectorResourceRecord,
        status_code=201,
    )
    def add_roblox_place(connector_id: str, body: RobloxPlaceAddRequest) -> ConnectorResourceRecord:
        try:
            account = ready().get_account(connector_id)
            if account is None:
                raise KeyError(connector_id)
            if account.provider != ConnectorProvider.ROBLOX:
                raise ConnectorStateError("CONNECTOR_PROVIDER_MISMATCH", "place registration requires Roblox connector")
            return roblox.add_place(
                connector_id=connector_id,
                universe_id=body.universe_id,
                place_id=body.place_id,
            )
        except Exception as exc:
            raise convert(exc) from exc

    @router.post(
        "/connector-resources/{resource_id}/bind",
        response_model=ConnectorResourceRecord,
    )
    def bind_connector_resource(
        resource_id: str,
        body: ConnectorResourceBindRequest,
    ) -> ConnectorResourceRecord:
        try:
            return ready().bind_resource_environment(
                resource_id=resource_id,
                environment_id=body.environment_id,
            )
        except Exception as exc:
            raise convert(exc) from exc

    @router.delete("/connectors/{connector_id}", response_model=ConnectorAccountRecord)
    def disconnect(connector_id: str) -> ConnectorAccountRecord:
        try:
            account = ready().get_account(connector_id)
            if account is None:
                raise KeyError(connector_id)
            credential_ref = repository.revoke(connector_id)
            if credential_ref:
                vault.delete(credential_ref)
            updated = repository.get_account(connector_id)
            assert updated is not None
            return updated
        except Exception as exc:
            raise convert(exc) from exc

    return router
