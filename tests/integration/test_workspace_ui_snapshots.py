from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.api.workspace_app import create_workspace_app
from amodex.storage.environment import PostgresEnvironmentRepository
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository
from amodex.storage.workspace import PostgresWorkspaceRepository


@pytest.fixture()
def ui_client() -> TestClient:
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
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/postbaseline/0002_environment_product_runners.sql").read_text(encoding="utf-8"))
        conn.execute(Path("migrations/postbaseline/0003_web_workspace.sql").read_text(encoding="utf-8"))
        conn.execute(Path("migrations/postbaseline/0004_connectors.sql").read_text(encoding="utf-8"))
        conn.execute(
            """
            TRUNCATE
                amodex_connector_oauth_states,
                amodex_connector_resources,
                amodex_connector_accounts,
                amodex_execution_drafts,
                amodex_workspace_attachments,
                amodex_workspace_memories,
                amodex_workspace_messages,
                amodex_workspace_chats,
                amodex_workspace_environments,
                amodex_workspace_projects
            CASCADE
            """
        )
    base = create_app(
        runs,
        product_repository=product,
        environment_repository=environments,
        environment_product_repository=environment_product,
    )
    app = create_workspace_app(base_app=base, workspace_repository=workspace)
    return TestClient(app)


def test_ui_bootstrap_project_and_chat_snapshots_use_product_shape(ui_client: TestClient) -> None:
    with ui_client as client:
        boot = client.get("/v1/ui/bootstrap")
        assert boot.status_code == 200
        body = boot.json()
        assert body["ready"] is True
        assert body["projects"] == []
        assert body["global_workspace"]["description"] == "__amodex_global_chat_container_v1__"

        project = client.post(
            "/v1/ui/projects",
            json={"name": "Fast Project", "description": "snapshot"},
        )
        assert project.status_code == 201
        project_id = project.json()["workspace_id"]

        project_view = client.get(f"/v1/ui/projects/{project_id}")
        assert project_view.status_code == 200
        project_snapshot = project_view.json()
        assert project_snapshot["project"]["name"] == "Fast Project"
        assert project_snapshot["chats"] == []
        assert project_snapshot["memories"] == []

        chat = client.post(
            f"/v1/ui/projects/{project_id}/chats",
            json={"title": "Instant chat", "active_environment_id": None},
        )
        assert chat.status_code == 201
        chat_id = chat.json()["chat_id"]

        sent = client.post(
            f"/v1/ui/chats/{chat_id}/messages",
            json={"role": "OWNER", "content": "histórico carregado"},
        )
        assert sent.status_code == 201

        chat_view = client.get(f"/v1/ui/chats/{chat_id}")
        assert chat_view.status_code == 200
        snapshot = chat_view.json()
        assert snapshot["chat"]["title"] == "Instant chat"
        assert snapshot["workspace"]["workspace_id"] == project_id
        assert [item["content"] for item in snapshot["messages"]] == ["histórico carregado"]
        assert snapshot["drafts"] == []

        second_boot = client.get("/v1/ui/bootstrap").json()
        assert [item["name"] for item in second_boot["projects"]] == ["Fast Project"]
        assert all(item["description"] != "__amodex_global_chat_container_v1__" for item in second_boot["projects"])


def test_connections_snapshot_never_exposes_credential_ref(ui_client: TestClient) -> None:
    with ui_client as client:
        project = client.post("/v1/ui/projects", json={"name": "Connections Project"}).json()
        boot = client.get("/v1/ui/connections/bootstrap")
        assert boot.status_code == 200
        assert any(item["workspace_id"] == project["workspace_id"] for item in boot.json()["projects"])

        snapshot = client.get(f"/v1/ui/connections/projects/{project['workspace_id']}")
        assert snapshot.status_code == 200
        assert snapshot.json()["accounts"] == []
        assert "credential_ref" not in snapshot.text
