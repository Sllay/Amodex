from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.runners.dispatch import RunnerDispatchError
from amodex.storage.environment import PostgresEnvironmentRepository
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository


class RecordingDispatcher:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    def dispatch(self, *, job_id: str) -> None:
        self.job_ids.append(job_id)


class AmbiguousDispatcher:
    def dispatch(self, *, job_id: str) -> None:
        raise RunnerDispatchError(f"simulated timeout for {job_id}")


@pytest.fixture()
def environment_product_state() -> tuple[
    PostgresRunRepository,
    PostgresProductRepository,
    PostgresEnvironmentRepository,
    PostgresEnvironmentProductRepository,
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
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/postbaseline/0002_environment_product_runners.sql").read_text(encoding="utf-8"))
        conn.execute(
            """
            TRUNCATE
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
    assert environments.schema_ready()
    assert environment_product.schema_ready()
    return runs, product, environments, environment_product


def _create_scoped_run(
    client: TestClient,
    *,
    name: str,
    engine: str,
    repo_url: str,
) -> tuple[dict, dict, dict, dict]:
    project = client.post("/v1/projects", json={"repo_url": repo_url, "name": name}).json()
    environment = client.post(
        "/v1/environments",
        json={"name": name, "engine": engine},
    ).json()
    bound = client.post(
        f"/v1/environments/{environment['environment_id']}/projects",
        json={"project_id": project["project_id"]},
    )
    assert bound.status_code == 200
    session = client.post(
        f"/v1/environments/{environment['environment_id']}/sessions",
        json={"title": f"{name} session", "domain_key": "gameplay"},
    ).json()
    run = client.post(
        "/v1/runs",
        json={"prompt": f"validate {name}", "project_id": project["project_id"]},
    ).json()
    run_bound = client.post(
        f"/v1/environments/{environment['environment_id']}/sessions/{session['session_id']}/runs/{run['run_id']}"
    )
    assert run_bound.status_code == 200
    return project, environment, session, run


def test_godot_environment_dispatch_claim_report_usage_and_dashboard(environment_product_state) -> None:
    runs, product, environments, environment_product = environment_product_state
    dispatcher = RecordingDispatcher()
    app = create_app(
        runs,
        product_repository=product,
        environment_repository=environments,
        environment_product_repository=environment_product,
        runner_dispatcher=dispatcher,
        runner_control_token="runner-test-secret",
    )

    with TestClient(app) as client:
        project, environment, session, run = _create_scoped_run(
            client,
            name="Godot Game",
            engine="godot",
            repo_url="https://github.com/example/godot-game",
        )
        dispatched = client.post(
            f"/v1/environments/{environment['environment_id']}/runner-jobs",
            json={
                "run_id": run["run_id"],
                "session_id": session["session_id"],
                "worker_profile": "implementation",
                "task_class": "ROUTINE",
                "write_paths": ["src/inventory.gd"],
            },
        )
        assert dispatched.status_code == 201
        body = dispatched.json()
        job = body["job"]
        assert body["dispatch"] == "GITHUB_ACTIONS"
        assert job["runner_kind"] == "ACTIONS_HEADLESS"
        assert job["state"] == "DISPATCHED"
        assert dispatcher.job_ids == [job["job_id"]]

        denied = client.post(f"/v1/runner-jobs/{job['job_id']}/claim")
        assert denied.status_code == 401

        auth = {"authorization": "Bearer runner-test-secret"}
        claimed = client.post(
            f"/v1/runner-jobs/{job['job_id']}/claim?github_run_id=12345",
            headers=auth,
        )
        assert claimed.status_code == 200
        claim = claimed.json()
        assert claim["repo_url"] == project["repo_url"]
        assert claim["job"]["state"] == "CLAIMED"
        assert "godot.headless" in claim["capabilities"]
        assert "godot.mcp-headless" in claim["capabilities"]

        running = client.post(f"/v1/runner-jobs/{job['job_id']}/running", headers=auth)
        assert running.status_code == 200
        assert running.json()["state"] == "RUNNING"

        completed = client.post(
            f"/v1/runner-jobs/{job['job_id']}/report",
            headers=auth,
            json={
                "state": "COMPLETED",
                "summary": "Godot headless + MCP validation passed",
                "result_commit": "a" * 40,
                "github_run_id": "12345",
                "evidence": {"godot": True, "mcp": True},
            },
        )
        assert completed.status_code == 200
        assert completed.json()["state"] == "COMPLETED"

        environments.record_usage(
            environment_id=environment["environment_id"],
            session_id=session["session_id"],
            run_id=run["run_id"],
            task_id="inventory",
            provider="groq",
            model="openai/gpt-oss-20b",
            input_tokens=1200,
            output_tokens=300,
            requests=1,
            outcome="PASS",
        )
        usage = client.get(f"/v1/environments/{environment['environment_id']}/usage")
        assert usage.status_code == 200
        assert usage.json()["input_tokens"] == 1200
        assert usage.json()["output_tokens"] == 300
        assert usage.json()["requests"] == 1
        assert usage.json()["by_provider"][0]["provider"] == "groq"

        dashboard = client.get("/v1/dashboard")
        assert dashboard.status_code == 200
        snap = dashboard.json()
        assert snap["environments"][0]["environment_id"] == environment["environment_id"]
        assert snap["environment_sessions"][0]["session_id"] == session["session_id"]
        assert snap["environment_usage"][0]["input_tokens"] == 1200
        assert snap["environment_runner_jobs"][0]["state"] == "COMPLETED"
        page = client.get("/")
        assert page.status_code == 200
        assert "Environments" in page.text
        assert "Quota / token telemetry" in page.text
        assert "Environment runner jobs" in page.text


def test_environment_scope_cannot_be_crossed_and_roblox_requires_desktop_runner(environment_product_state) -> None:
    runs, product, environments, environment_product = environment_product_state
    dispatcher = RecordingDispatcher()
    app = create_app(
        runs,
        product_repository=product,
        environment_repository=environments,
        environment_product_repository=environment_product,
        runner_dispatcher=dispatcher,
        runner_control_token="runner-test-secret",
    )

    with TestClient(app) as client:
        _, godot_env, godot_session, godot_run = _create_scoped_run(
            client,
            name="Godot",
            engine="godot",
            repo_url="https://github.com/example/godot",
        )
        _, roblox_env, roblox_session, roblox_run = _create_scoped_run(
            client,
            name="Roblox",
            engine="roblox",
            repo_url="https://github.com/example/roblox",
        )

        crossed = client.post(
            f"/v1/environments/{roblox_env['environment_id']}/runner-jobs",
            json={
                "run_id": godot_run["run_id"],
                "session_id": roblox_session["session_id"],
                "task_class": "STANDARD",
            },
        )
        assert crossed.status_code == 409
        assert crossed.json()["detail"]["code"] == "PROJECT_ENVIRONMENT_MISMATCH"

        roblox = client.post(
            f"/v1/environments/{roblox_env['environment_id']}/runner-jobs",
            json={
                "run_id": roblox_run["run_id"],
                "session_id": roblox_session["session_id"],
                "task_class": "STANDARD",
            },
        )
        assert roblox.status_code == 201
        assert roblox.json()["dispatch"] == "DESKTOP_RUNNER_REQUIRED"
        assert roblox.json()["job"]["runner_kind"] == "DESKTOP_STUDIO"
        assert roblox.json()["job"]["state"] == "QUEUED"
        assert dispatcher.job_ids == []

        # A Godot job still uses Actions and proves the dispatcher is scoped by engine.
        godot = client.post(
            f"/v1/environments/{godot_env['environment_id']}/runner-jobs",
            json={
                "run_id": godot_run["run_id"],
                "session_id": godot_session["session_id"],
                "task_class": "STANDARD",
            },
        )
        assert godot.status_code == 201
        assert dispatcher.job_ids == [godot.json()["job"]["job_id"]]


def test_ambiguous_actions_dispatch_is_never_blindly_retried_but_can_be_claimed(environment_product_state) -> None:
    runs, product, environments, environment_product = environment_product_state
    app = create_app(
        runs,
        product_repository=product,
        environment_repository=environments,
        environment_product_repository=environment_product,
        runner_dispatcher=AmbiguousDispatcher(),
        runner_control_token="runner-test-secret",
    )

    with TestClient(app) as client:
        _, environment, session, run = _create_scoped_run(
            client,
            name="Ambiguous Godot",
            engine="godot",
            repo_url="https://github.com/example/ambiguous",
        )
        response = client.post(
            f"/v1/environments/{environment['environment_id']}/runner-jobs",
            json={
                "run_id": run["run_id"],
                "session_id": session["session_id"],
                "task_class": "MICRO",
            },
        )
        assert response.status_code == 502
        detail = response.json()["detail"]
        assert detail["code"] == "RUNNER_DISPATCH_UNKNOWN"
        job_id = detail["job_id"]
        stored = client.get(f"/v1/runner-jobs/{job_id}")
        assert stored.status_code == 200
        assert stored.json()["state"] == "DISPATCH_UNKNOWN"

        # If GitHub actually received the dispatch before the timeout, that exact job may claim.
        claimed = client.post(
            f"/v1/runner-jobs/{job_id}/claim?github_run_id=late-123",
            headers={"authorization": "Bearer runner-test-secret"},
        )
        assert claimed.status_code == 200
        assert claimed.json()["job"]["state"] == "CLAIMED"
