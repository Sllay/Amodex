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
def workspace_state() -> tuple[
    PostgresRunRepository,
    PostgresProductRepository,
    PostgresEnvironmentRepository,
    PostgresEnvironmentProductRepository,
    PostgresWorkspaceRepository,
]:
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
        conn.execute(
            """
            TRUNCATE
                amodex_execution_drafts,
                amodex_workspace_attachments,
                amodex_workspace_memories,
                amodex_workspace_messages,
                amodex_workspace_chats,
                amodex_workspace_environments,
                amodex_workspace_projects,
                amodex_environment_runner_jobs,
                amodex_environment_usage_events,
                amodex_environment_budget_policies,
                amodex_memories,
                amodex_session_runs,
                amodex_sessions,
                amodex_environment_projects,
                amodex_environments,
                amodex_audit_events,
                amodex_checkpoint_views,
                amodex_evidence_views,
                amodex_task_views,
                amodex_runners,
                amodex_provider_connections,
                amodex_approvals,
                amodex_run_controls,
                amodex_promotions,
                amodex_candidates,
                amodex_provider_availability_observations,
                amodex_model_measurements,
                amodex_run_events,
                amodex_runs,
                amodex_projects
            CASCADE
            """
        )
    assert workspace.schema_ready()
    return runs, product, environments, environment_product, workspace


def _client(workspace_state) -> TestClient:
    runs, product, environments, environment_product, workspace = workspace_state
    base = create_app(
        runs,
        product_repository=product,
        environment_repository=environments,
        environment_product_repository=environment_product,
    )
    return TestClient(
        create_workspace_app(base_app=base, workspace_repository=workspace)
    )


def test_workspace_web_shell_and_existing_control_plane_routes_survive(workspace_state) -> None:
    with _client(workspace_state) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Amodex Workspace" in page.text
        assert "Execution Drafts" in page.text
        assert "AI gated" in page.text
        assert "Passo 2: GitHub/Roblox Connectors" in page.text

        legacy = client.get("/legacy")
        assert legacy.status_code == 200
        assert "Environment runner jobs" in legacy.text

        status = client.get("/v1/system/status")
        assert status.status_code == 200
        workspace_status = client.get("/v1/workspace/status")
        assert workspace_status.status_code == 200
        assert workspace_status.json()["chat_inference"] == "GATED_BENCHMARK_PENDING"
        assert workspace_status.json()["execution_authority"] == "ENVIRONMENT_ONLY"


def test_project_chat_memory_and_draft_happy_path(workspace_state) -> None:
    with _client(workspace_state) as client:
        project = client.post(
            "/v1/workspaces",
            json={"name": "Black Island", "description": "Godot + Roblox project space"},
        )
        assert project.status_code == 201
        workspace = project.json()

        environment = client.post(
            "/v1/environments", json={"name": "Godot Dev", "engine": "godot"}
        ).json()
        bound = client.post(
            f"/v1/workspaces/{workspace['workspace_id']}/environments",
            json={"environment_id": environment["environment_id"], "label": "Godot Dev"},
        )
        assert bound.status_code == 201

        chat = client.post(
            f"/v1/workspaces/{workspace['workspace_id']}/chats",
            json={
                "title": "Sistema de farming",
                "active_environment_id": environment["environment_id"],
            },
        )
        assert chat.status_code == 201
        chat_id = chat.json()["chat_id"]

        message = client.post(
            f"/v1/chats/{chat_id}/messages",
            json={"role": "OWNER", "content": "Quero melhorar a coleta de madeira."},
        )
        assert message.status_code == 201
        messages = client.get(f"/v1/chats/{chat_id}/messages").json()
        assert [item["role"] for item in messages] == ["OWNER"]

        memory = client.post(
            f"/v1/workspaces/{workspace['workspace_id']}/memories",
            json={
                "content": "Coleta precisa responder visualmente ao impacto.",
                "source_ref": f"chat:{chat_id}",
            },
        )
        assert memory.status_code == 201
        assert memory.json()["status"] == "CANDIDATE"
        approved = client.post(
            f"/v1/memories/{memory.json()['memory_id']}/decision",
            json={"status": "APPROVED"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "APPROVED"

        draft = client.post(
            f"/v1/chats/{chat_id}/drafts",
            json={
                "title": "Farming impact V1",
                "objective": "Melhorar feedback de coleta",
                "instructions": "Preservar arquitetura existente e modularizar dano/coleta.",
                "validation": "Godot headless + testes do projeto",
                "environment_id": environment["environment_id"],
            },
        )
        assert draft.status_code == 201
        assert draft.json()["status"] == "DRAFT"
        ready = client.post(
            f"/v1/drafts/{draft.json()['draft_id']}/state",
            json={"status": "READY"},
        )
        assert ready.status_code == 200
        assert ready.json()["status"] == "READY"


def test_workspace_boundaries_reject_cross_project_environment_and_public_amodex_impersonation(workspace_state) -> None:
    with _client(workspace_state) as client:
        a = client.post("/v1/workspaces", json={"name": "Godot Project"}).json()
        b = client.post("/v1/workspaces", json={"name": "Roblox Project"}).json()
        env_a = client.post(
            "/v1/environments", json={"name": "Godot Dev", "engine": "godot"}
        ).json()
        env_b = client.post(
            "/v1/environments", json={"name": "Roblox Dev", "engine": "roblox"}
        ).json()

        first = client.post(
            f"/v1/workspaces/{a['workspace_id']}/environments",
            json={"environment_id": env_a["environment_id"]},
        )
        assert first.status_code == 201
        crossed = client.post(
            f"/v1/workspaces/{b['workspace_id']}/environments",
            json={"environment_id": env_a["environment_id"]},
        )
        assert crossed.status_code == 409
        assert crossed.json()["detail"]["code"] == "ENVIRONMENT_ALREADY_BOUND_TO_WORKSPACE"

        # Roblox env exists, but is not bound to workspace A.
        foreign_chat = client.post(
            f"/v1/workspaces/{a['workspace_id']}/chats",
            json={"title": "bad", "active_environment_id": env_b["environment_id"]},
        )
        assert foreign_chat.status_code == 409
        assert foreign_chat.json()["detail"]["code"] == "CHAT_ENVIRONMENT_NOT_BOUND"

        chat = client.post(
            f"/v1/workspaces/{a['workspace_id']}/chats",
            json={"title": "safe", "active_environment_id": env_a["environment_id"]},
        ).json()
        impersonation = client.post(
            f"/v1/chats/{chat['chat_id']}/messages",
            json={"role": "AMODEX", "content": "I executed something."},
        )
        assert impersonation.status_code == 403
        assert impersonation.json()["detail"]["code"] == "CHAT_ROLE_NOT_PUBLICLY_WRITABLE"

        foreign_draft = client.post(
            f"/v1/chats/{chat['chat_id']}/drafts",
            json={
                "title": "crossed",
                "objective": "bad",
                "instructions": "bad",
                "environment_id": env_b["environment_id"],
            },
        )
        assert foreign_draft.status_code == 409
        assert foreign_draft.json()["detail"]["code"] == "DRAFT_ENVIRONMENT_NOT_BOUND"


def test_draft_cannot_claim_execution_without_real_run_handoff(workspace_state) -> None:
    with _client(workspace_state) as client:
        workspace = client.post("/v1/workspaces", json={"name": "Game"}).json()
        chat = client.post(
            f"/v1/workspaces/{workspace['workspace_id']}/chats",
            json={"title": "Planning"},
        ).json()
        draft = client.post(
            f"/v1/chats/{chat['chat_id']}/drafts",
            json={"title": "Task", "objective": "x", "instructions": "y"},
        ).json()
        denied = client.post(
            f"/v1/drafts/{draft['draft_id']}/state",
            json={"status": "EXECUTED"},
        )
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "DRAFT_EXECUTION_REQUIRES_RUN_HANDOFF"
