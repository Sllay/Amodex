from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.product.models import CheckpointView, EvidenceView, RunnerState, TaskView
from amodex.providers.capabilities import BillingClass, ProviderAvailability, ProviderHealth
from amodex.providers.governor import ProviderGovernor
from amodex.providers.simulator import DeterministicProvider
from amodex.runtime.models import RunState
from amodex.runtime.worker import Stage1Worker
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository


@pytest.fixture()
def stage6_state() -> tuple[PostgresRunRepository, PostgresProductRepository]:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    runs = PostgresRunRepository(dsn)
    runs.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8"))
    routing = PostgresRoutingRepository(dsn)
    routing.migrate(Path("migrations/stage5/0001_routing_measurements.sql"))
    product = PostgresProductRepository(dsn)
    product.migrate(Path("migrations/stage6/0001_product_surfaces.sql"))
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            TRUNCATE
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
    return runs, product


def test_pause_resume_and_cancel_are_durable_and_worker_enforced(stage6_state) -> None:
    runs, product = stage6_state
    app = create_app(runs, product_repository=product)
    worker = Stage1Worker(
        repository=runs,
        provider=DeterministicProvider(),
        governor=ProviderGovernor.stage1_default(),
        worker_id="stage6-control-worker",
    )

    with TestClient(app) as client:
        project = client.post("/v1/projects", json={"repo_url": "https://github.com/example/game"}).json()
        created = client.post(
            "/v1/runs",
            json={"prompt": "durable objective", "project_id": project["project_id"]},
        )
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        paused = client.post(f"/v1/runs/{run_id}/pause", json={"reason": "owner pause"})
        assert paused.status_code == 200
        assert client.get(f"/v1/runs/{run_id}").json()["state"] == "PAUSED"

    # A closed client/terminal owns no execution state. Paused work remains durable.
    assert worker.process_once() is None

    with TestClient(app) as client:
        resumed = client.post(f"/v1/runs/{run_id}/resume", json={"reason": "continue"})
        assert resumed.status_code == 200
        assert client.get(f"/v1/runs/{run_id}").json()["state"] == "QUEUED"

    completed = worker.process_once()
    assert completed is not None
    assert completed.state == RunState.COMPLETED
    assert completed.result_text == "simulated:durable objective"

    second = runs.create_run(prompt="cancel before provider", project_id=project["project_id"])
    with TestClient(app) as client:
        cancelled = client.post(f"/v1/runs/{second.run_id}/cancel", json={"reason": "not needed"})
        assert cancelled.status_code == 200
        assert client.get(f"/v1/runs/{second.run_id}").json()["state"] == "CANCELLED"
    assert worker.process_once() is None


def test_web_dashboard_projects_provider_runner_approval_and_evidence(stage6_state, monkeypatch) -> None:
    runs, product = stage6_state
    app = create_app(runs, product_repository=product)
    now = datetime.now(timezone.utc)

    with TestClient(app) as client:
        project = client.post(
            "/v1/projects",
            json={"repo_url": "https://github.com/example/site", "name": "Example Site"},
        ).json()
        run = client.post(
            "/v1/runs",
            json={"prompt": "improve navigation", "project_id": project["project_id"]},
        ).json()

        monkeypatch.setenv("GROQ_API_KEY", "stage6-test-secret-not-stored")
        connection = client.post(
            "/v1/provider-connections",
            json={
                "provider": "groq",
                "credential_pool_id": "groq-free-primary",
                "secret_ref": "env://GROQ_API_KEY",
                "billing_class": "free",
                "enabled": True,
            },
        )
        assert connection.status_code == 200
        assert connection.json()["secret_present"] is True
        assert "stage6-test-secret" not in str(connection.json())

        heartbeat = client.post(
            "/v1/runners/runner-1/heartbeat",
            json={
                "profile": "general",
                "state": "RUNNING",
                "capabilities": ["code_read", "test_execute"],
                "detail": "healthy",
            },
        )
        assert heartbeat.status_code == 200
        approval = client.post(
            "/v1/approvals",
            json={
                "action": "PROMOTE",
                "subject_type": "candidate",
                "subject_id": "candidate-example",
                "impact": "would update the validated target branch",
            },
        ).json()

    product.upsert_task_view(
        TaskView(
            run_id=run["run_id"], task_id="task-1", objective="navigation implementation",
            state="RUNNING", dependencies=(), worker_profile="implementation", updated_at=now,
        )
    )
    product.upsert_evidence_view(
        EvidenceView(
            evidence_id="evidence-1", run_id=run["run_id"], task_id="task-1",
            subject_sha="a" * 40, validator_id="pytest", result="PASS", summary="tests passed",
            artifact_sha256=("b" * 64,), observed_at=now,
        )
    )
    product.upsert_checkpoint_view(
        CheckpointView(
            checkpoint_id="checkpoint-1", run_id=run["run_id"], task_id="task-1",
            checkpoint_class="VALIDATED", commit_sha="a" * 40, evidence_ids=("evidence-1",), created_at=now,
        )
    )
    PostgresRoutingRepository(os.environ["AMODEX_TEST_DATABASE_URL"]).record_availability(
        ProviderAvailability(
            provider="groq", credential_pool_id="groq-free-primary", billing_class=BillingClass.FREE,
            health=ProviderHealth.HEALTHY, quota_available=True, free_capacity_ratio=0.75,
            observed_at=now, reason="live free capacity available",
        )
    )

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        for heading in (
            "Projects", "Runs", "Task DAG / status", "Provider availability / quota",
            "Runner status", "Approvals / blockers", "Checkpoints / candidates / promotions",
            "Validation evidence / artifacts", "Event / audit timeline",
        ):
            assert heading in page.text

        dashboard = client.get("/v1/dashboard")
        assert dashboard.status_code == 200
        body = dashboard.json()
        assert body["zero_cost_mode"] is True
        assert body["projects"][0]["project_id"] == project["project_id"]
        assert body["tasks"][0]["task_id"] == "task-1"
        assert body["runners"][0]["runner_id"] == "runner-1"
        assert body["checkpoints"][0]["checkpoint_id"] == "checkpoint-1"
        assert body["evidence"][0]["evidence_id"] == "evidence-1"
        assert any(item["provider"] == "groq" and item["quota_available"] for item in body["providers"])
        assert any(item["approval_id"] == approval["approval_id"] for item in body["approvals"])
        assert any(item["kind"] == "APPROVAL" for item in body["blockers"])
        assert body["timeline"]

        approved = client.post(f"/v1/approvals/{approval['approval_id']}/approve")
        assert approved.status_code == 200
        assert approved.json()["status"] == "APPROVED"


def test_raw_provider_secret_is_rejected_and_retention_is_preview_only(stage6_state) -> None:
    runs, product = stage6_state
    app = create_app(runs, product_repository=product)
    with TestClient(app) as client:
        rejected = client.post(
            "/v1/provider-connections",
            json={
                "provider": "nvidia",
                "credential_pool_id": "nvidia-free-primary",
                "secret_ref": "nvapi-this-must-never-be-accepted",
                "billing_class": "free",
                "enabled": True,
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "SECRET_REFERENCE_REQUIRED"

        preview = client.get("/v1/operations/retention/preview?days=30")
        assert preview.status_code == 200
        assert preview.json() == {"runs": 0, "events": 0}

        status = client.get("/v1/system/status")
        assert status.status_code == 200
        assert status.json()["zero_cost_mode"] is True
        assert status.json()["schema_ready"] is True
