from __future__ import annotations

import os
from pathlib import Path

import psycopg

from amodex.providers.base import ProviderFailure
from amodex.providers.governor import ProviderGovernor
from amodex.runtime.models import EventKind, RunState
from amodex.runtime.worker import Stage1Worker
from amodex.storage.postgres import PostgresRunRepository


class FailingProvider:
    provider_name = "gemini"

    def __init__(self, failure: ProviderFailure) -> None:
        self.failure = failure
        self.calls = 0

    def generate(self, *, model: str, prompt: str, request_key: str):
        self.calls += 1
        raise self.failure


def _repo() -> PostgresRunRepository:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    repo = PostgresRunRepository(dsn)
    repo.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE amodex_run_events, amodex_runs CASCADE")
    return repo


def test_malformed_provider_output_fails_without_corrupting_authority() -> None:
    repo = _repo()
    created = repo.create_run(prompt="malformed output")
    provider = FailingProvider(ProviderFailure(code="MALFORMED_PROVIDER_OUTPUT", message="synthetic malformed output"))
    worker = Stage1Worker(repository=repo, provider=provider, governor=ProviderGovernor.stage1_default(), worker_id="failure-worker")
    result = worker.process_once()
    assert result is not None
    assert result.run_id == created.run_id
    assert result.state == RunState.FAILED
    assert result.last_error_code == "MALFORMED_PROVIDER_OUTPUT"
    assert result.result_text is None
    assert result.lease_owner is None
    assert provider.calls == 1
    events = repo.list_events(created.run_id)
    assert events[-1].kind == EventKind.RUN_FAILED
    assert sum(event.kind == EventKind.RUN_COMPLETED for event in events) == 0


def test_rate_limit_waits_and_never_uses_paid_fallback() -> None:
    repo = _repo()
    created = repo.create_run(prompt="rate limit")
    provider = FailingProvider(ProviderFailure(code="RATE_LIMITED", message="free quota unavailable", retry_after_seconds=60.0))
    worker = Stage1Worker(repository=repo, provider=provider, governor=ProviderGovernor.stage1_default(), worker_id="rate-limit-worker")
    result = worker.process_once()
    assert result is not None
    assert result.run_id == created.run_id
    assert result.state == RunState.WAITING_PROVIDER
    assert result.last_error_code == "RATE_LIMITED"
    assert result.lease_owner is None
    events = repo.list_events(created.run_id)
    assert events[-1].kind == EventKind.PROVIDER_WAIT
    assert events[-1].payload["paid_fallback"] is False
