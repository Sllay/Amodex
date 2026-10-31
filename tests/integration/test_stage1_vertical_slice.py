from __future__ import annotations
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from amodex.api.app import create_app
from amodex.providers.governor import ProviderGovernor
from amodex.providers.simulator import DeterministicProvider
from amodex.runtime.models import EventKind, RunState
from amodex.runtime.worker import Stage1Worker
from amodex.storage.postgres import PostgresRunRepository, StaleLeaseError


@pytest.fixture()
def repository() -> PostgresRunRepository:
    repo = PostgresRunRepository(os.environ["AMODEX_TEST_DATABASE_URL"])
    repo.migrate(Path("migrations/0001_stage1_runtime.sql"))
    return repo


def test_create_disconnect_worker_reconnect(repository: PostgresRunRepository) -> None:
    app = create_app(repository)
    with TestClient(app) as client:
        created = client.post("/v1/runs", json={"schema_version":1,"prompt":"hello"}); assert created.status_code == 201; run_id = created.json()["run_id"]
    worker = Stage1Worker(repository=repository, provider=DeterministicProvider(), governor=ProviderGovernor.stage1_default(), worker_id="stage1-test-worker")
    completed = worker.process_once(); assert completed is not None; assert completed.run_id == run_id; assert completed.state == RunState.COMPLETED; assert completed.result_text == "simulated:hello"
    with TestClient(app) as client:
        observed = client.get(f"/v1/runs/{run_id}"); assert observed.status_code == 200; assert observed.json()["state"] == "COMPLETED"
        events = client.get(f"/v1/runs/{run_id}/events"); assert events.status_code == 200
        assert [event["kind"] for event in events.json()] == ["RUN_CREATED","RUN_CLAIMED","PROVIDER_ADMITTED","PROVIDER_REQUEST_STARTED","PROVIDER_REQUEST_COMPLETED","RUN_COMPLETED"]


def test_quota_exhaustion_enters_waiting_provider(repository: PostgresRunRepository) -> None:
    run = repository.create_run(prompt="do not spend")
    worker = Stage1Worker(repository=repository, provider=DeterministicProvider(), governor=ProviderGovernor.stage1_default(), worker_id="quota-test-worker")
    result = worker.process_once(free_quota_available=False); assert result is not None; assert result.run_id == run.run_id; assert result.state == RunState.WAITING_PROVIDER; assert result.last_error_code == "FREE_QUOTA_UNAVAILABLE"; assert result.lease_owner is None


def test_stale_fencing_token_is_rejected(repository: PostgresRunRepository) -> None:
    repository.create_run(prompt="lease test"); claimed = repository.claim_next(worker_id="worker-a", lease_seconds=30); assert claimed is not None
    with pytest.raises(StaleLeaseError):
        repository.transition(run_id=claimed.run_id, worker_id="worker-a", fencing_token=claimed.fencing_token + 1, state=RunState.RUNNING, kind=EventKind.PROVIDER_ADMITTED)
