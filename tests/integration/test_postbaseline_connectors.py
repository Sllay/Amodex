from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.api.workspace_app import create_workspace_app
from amodex.connectors.clients import GitHubAppConnector, RobloxOAuthConnector
from amodex.connectors.oauth import GitHubAppConfig, RobloxOAuthConfig
from amodex.connectors.vault import MemoryCredentialVault
from amodex.storage.connectors import ConnectorStateError, PostgresConnectorRepository
from amodex.storage.environment import PostgresEnvironmentRepository
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository
from amodex.storage.workspace import PostgresWorkspaceRepository


@pytest.fixture()
def connector_state(monkeypatch):
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    runs = PostgresRunRepository(dsn)
    runs.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8"))
    PostgresRoutingRepository(dsn).migrate(Path("migrations/stage5/0001_routing_measurements.sql"))
    product = PostgresProductRepository(dsn)
    product.migrate(Path("migrations/stage6/0001_product_surfaces.sql"))
    environments = PostgresEnvironmentRepository(dsn)
    environments.migrate(Path("migrations/postbaseline/0001_environment_isolation_budget.sql"))
    environment_product = PostgresEnvironmentProductRepository(dsn)
    workspace = PostgresWorkspaceRepository(dsn)
    connectors = PostgresConnectorRepository(dsn)
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/postbaseline/0002_environment_product_runners.sql").read_text(encoding="utf-8"))
        conn.execute(Path("migrations/postbaseline/0003_web_workspace.sql").read_text(encoding="utf-8"))
        conn.execute(Path("migrations/postbaseline/0004_connectors.sql").read_text(encoding="utf-8"))
        conn.execute(
            """
            TRUNCATE
                amodex_connector_oauth_states,amodex_connector_resources,amodex_connector_accounts,
                amodex_execution_drafts,amodex_workspace_attachments,amodex_workspace_memories,
                amodex_workspace_messages,amodex_workspace_chats,amodex_workspace_environments,
                amodex_workspace_projects,amodex_environment_runner_jobs,
                amodex_environment_usage_events,amodex_environment_budget_policies,amodex_memories,
                amodex_session_runs,amodex_sessions,amodex_environment_projects,amodex_environments,
                amodex_audit_events,amodex_checkpoint_views,amodex_evidence_views,amodex_task_views,
                amodex_runners,amodex_provider_connections,amodex_approvals,amodex_run_controls,
                amodex_promotions,amodex_candidates,amodex_provider_availability_observations,
                amodex_model_measurements,amodex_run_events,amodex_runs,amodex_projects CASCADE
            """
        )
    vault = MemoryCredentialVault()
    monkeypatch.setenv("AMODEX_ROBLOX_CLIENT_SECRET", "rb-secret")

    def github_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gh-secret-token", "token_type": "bearer"})
        if request.method == "GET" and path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "Sllay", "avatar_url": "https://example/avatar"})
        if request.method == "GET" and path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 7}]})
        if request.method == "GET" and path == "/user/installations/7/repositories":
            return httpx.Response(200, json={"repositories": [{"id": 99, "full_name": "Sllay/Black-Islands", "html_url": "https://github.com/Sllay/Black-Islands", "private": True, "default_branch": "main"}]})
        return httpx.Response(404, json={"path": path})

    def roblox_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/oauth/v1/token":
            return httpx.Response(200, json={"access_token": "rb-secret-token", "refresh_token": "rb-refresh", "token_type": "Bearer"})
        if request.method == "GET" and path == "/oauth/v1/userinfo":
            return httpx.Response(200, json={"sub": "77", "preferred_username": "BadKing"})
        if request.method == "POST" and path == "/oauth/v1/token/resources":
            return httpx.Response(200, json={"resource_infos": [{"universeId": "123"}]})
        if request.method == "GET" and path == "/cloud/v2/universes/123":
            return httpx.Response(200, json={"path": "universes/123", "displayName": "Black Island Test"})
        if request.method == "GET" and path == "/cloud/v2/universes/123/places/456":
            return httpx.Response(200, json={"path": "universes/123/places/456", "displayName": "Main Place"})
        return httpx.Response(404, json={"path": path})

    github = GitHubAppConnector(
        config=GitHubAppConfig(
            app_slug="amodex-test",
            client_id="github-client",
            redirect_uri="https://amodex.example/v1/connectors/github/callback",
        ),
        repository=connectors,
        vault=vault,
        http=httpx.Client(transport=httpx.MockTransport(github_handler)),
    )
    roblox = RobloxOAuthConnector(
        config=RobloxOAuthConfig(
            client_id="roblox-client",
            redirect_uri="https://amodex.example/v1/connectors/roblox/callback",
            scopes=("openid", "profile", "universe:read"),
            client_secret_ref="env://AMODEX_ROBLOX_CLIENT_SECRET",
        ),
        repository=connectors,
        vault=vault,
        http=httpx.Client(transport=httpx.MockTransport(roblox_handler)),
    )
    base = create_app(
        runs,
        product_repository=product,
        environment_repository=environments,
        environment_product_repository=environment_product,
    )
    app = create_workspace_app(
        base_app=base,
        workspace_repository=workspace,
        connector_repository=connectors,
        connector_vault=vault,
        github_connector=github,
        roblox_connector=roblox,
    )
    return dsn, TestClient(app), workspace, connectors, vault, github, roblox


def _state_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def test_github_pkce_state_token_vault_and_repository_discovery(connector_state) -> None:
    dsn, client, _workspace_repo, connectors, vault, _github, _roblox = connector_state
    workspace = client.post("/v1/workspaces", json={"name": "Black Island"}).json()
    start = client.post(f"/v1/workspaces/{workspace['workspace_id']}/connectors/github/start")
    assert start.status_code == 200
    body = start.json()
    assert "code_challenge=" in body["authorization_url"]
    assert body["installation_url"].startswith("https://github.com/apps/amodex-test/")
    state = _state_from(body["authorization_url"])

    with psycopg.connect(dsn) as conn:
        raw = conn.execute("SELECT state_hash,verifier_ref FROM amodex_connector_oauth_states").fetchone()
        dump = " ".join(str(x) for x in raw)
    assert state not in dump
    assert "memory-vault://" in raw[1]

    callback = client.get(
        "/v1/connectors/github/callback",
        params={"code": "good-code", "state": state, "installation_id": "999999"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    accounts = client.get(f"/v1/workspaces/{workspace['workspace_id']}/connectors").json()
    assert len(accounts) == 1
    connector = accounts[0]
    assert connector["external_account_name"] == "Sllay"
    assert connector["credential_ref"].startswith("memory-vault://")
    with psycopg.connect(dsn) as conn:
        text = str(conn.execute("SELECT credential_ref,metadata FROM amodex_connector_accounts").fetchone())
    assert "gh-secret-token" not in text
    assert "999999" not in text

    refreshed = client.post(f"/v1/connectors/{connector['connector_id']}/refresh")
    assert refreshed.status_code == 200
    resources = refreshed.json()["resources"]
    assert [item["name"] for item in resources] == ["Sllay/Black-Islands"]
    assert resources[0]["metadata"]["installation_id"] == "7"

    with pytest.raises(ConnectorStateError) as exc:
        connectors.consume_oauth_state(raw_state=state, provider=connectors.get_account(connector["connector_id"]).provider)
    assert exc.value.code == "OAUTH_STATE_ALREADY_USED"


def test_roblox_oauth_universe_place_and_cross_workspace_fencing(connector_state) -> None:
    _dsn, client, _workspace_repo, _connectors, _vault, _github, _roblox = connector_state
    a = client.post("/v1/workspaces", json={"name": "Roblox Game"}).json()
    b = client.post("/v1/workspaces", json={"name": "Other Game"}).json()
    env_a = client.post("/v1/environments", json={"name": "Roblox Dev", "engine": "roblox"}).json()
    env_b = client.post("/v1/environments", json={"name": "Other Godot", "engine": "godot"}).json()
    assert client.post(f"/v1/workspaces/{a['workspace_id']}/environments", json={"environment_id": env_a["environment_id"]}).status_code == 201
    assert client.post(f"/v1/workspaces/{b['workspace_id']}/environments", json={"environment_id": env_b["environment_id"]}).status_code == 201

    start = client.post(f"/v1/workspaces/{a['workspace_id']}/connectors/roblox/start").json()
    assert "code_challenge=" in start["authorization_url"]
    state = _state_from(start["authorization_url"])
    callback = client.get(
        "/v1/connectors/roblox/callback",
        params={"code": "roblox-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    connector = client.get(f"/v1/workspaces/{a['workspace_id']}/connectors").json()[0]
    refreshed = client.post(f"/v1/connectors/{connector['connector_id']}/refresh")
    assert refreshed.status_code == 200
    universes = refreshed.json()["resources"]
    assert universes[0]["external_id"] == "123"
    assert universes[0]["name"] == "Black Island Test"

    place = client.post(
        f"/v1/connectors/{connector['connector_id']}/roblox/places",
        json={"universe_id": "123", "place_id": "456"},
    )
    assert place.status_code == 201
    resource = place.json()
    assert resource["name"] == "Main Place"

    bad_bind = client.post(
        f"/v1/connector-resources/{resource['resource_id']}/bind",
        json={"environment_id": env_b["environment_id"]},
    )
    assert bad_bind.status_code == 409
    assert bad_bind.json()["detail"]["code"] == "RESOURCE_ENVIRONMENT_NOT_IN_WORKSPACE"
    good_bind = client.post(
        f"/v1/connector-resources/{resource['resource_id']}/bind",
        json={"environment_id": env_a["environment_id"]},
    )
    assert good_bind.status_code == 200
    assert good_bind.json()["bound_environment_id"] == env_a["environment_id"]


def test_disconnect_revokes_account_and_deletes_credential(connector_state) -> None:
    _dsn, client, _workspace_repo, _connectors, vault, _github, _roblox = connector_state
    workspace = client.post("/v1/workspaces", json={"name": "Game"}).json()
    start = client.post(f"/v1/workspaces/{workspace['workspace_id']}/connectors/github/start").json()
    state = _state_from(start["authorization_url"])
    client.get("/v1/connectors/github/callback", params={"code": "code", "state": state}, follow_redirects=False)
    connector = client.get(f"/v1/workspaces/{workspace['workspace_id']}/connectors").json()[0]
    ref = connector["credential_ref"]
    assert vault.get(ref)["access_token"] == "gh-secret-token"
    disconnected = client.delete(f"/v1/connectors/{connector['connector_id']}")
    assert disconnected.status_code == 200
    assert disconnected.json()["state"] == "REVOKED"
    with pytest.raises(KeyError):
        vault.get(ref)


def test_connections_ui_and_status_are_visual_and_config_aware(connector_state) -> None:
    _dsn, client, *_ = connector_state
    page = client.get("/")
    assert page.status_code == 200
    assert "/connections" in page.text
    connections = client.get("/connections")
    assert connections.status_code == 200
    assert "Connections & Resources" in connections.text
    status = client.get("/v1/connectors/status")
    assert status.status_code == 200
    assert {item["provider"] for item in status.json()} == {"GITHUB", "ROBLOX"}
