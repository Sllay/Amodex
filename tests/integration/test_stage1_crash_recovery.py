from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

from amodex.contracts.ids import new_uuid7
from amodex.providers.governor import ProviderGovernor
from amodex.providers.simulator import DeterministicProvider
from amodex.runtime.models import EventKind, ProviderRequestState, RunState
from amodex.runtime.worker import Stage1Worker
from amodex.storage.postgres import PostgresRunRepository, StaleLeaseError


class CountingProvider(DeterministicProvider):
    def __init__(self) -> None:
        super().__init__(prefix="recovered")
        self.calls = 0

    def generate(self, *, model: str, prompt: str, request_key: str):
        self.calls += 1
        return super().generate(model=model, prompt=prompt, request_key=request_key)


@pytest.fixture()
def repository() -> PostgresRunRepository:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    repo = PostgresRunRepository(dsn)
    repo.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE amodex_run_events, amodex_runs CASCADE")
    return repo


def _spawn_crashing_worker(*, run_id: str, after_intent: bool) -> tuple[subprocess.Popen[str], int]:
    child = r"""
import os
import time

from amodex.contracts.ids import new_uuid7
from amodex.runtime.models import EventKind, RunState
from amodex.storage.postgres import PostgresRunRepository

repo = PostgresRunRepository(os.environ["AMODEX_TEST_DATABASE_URL"])
run_id = os.environ["AMODEX_CRASH_RUN_ID"]
after_intent = os.environ["AMODEX_CRASH_AFTER_INTENT"] == "1"

claimed = repo.claim_next(worker_id="crashed-worker", lease_seconds=1)
assert claimed is not None
assert claimed.run_id == run_id

running = repo.transition(
    run_id=run_id,
    worker_id="crashed-worker",
    fencing_token=claimed.fencing_token,
    state=RunState.RUNNING,
    kind=EventKind.PROVIDER_ADMITTED,
    provider="gemini",
    model="gemini-3.5-flash-lite",
    payload={"phase": "pre-provider"},
)

if after_intent:
    repo.mark_provider_request_started(
        run_id=run_id,
        worker_id="crashed-worker",
        fencing_token=running.fencing_token,
        request_key=new_uuid7(),
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )

print(f"READY {claimed.fencing_token}", flush=True)
time.sleep(300)
"""
    env = os.environ.copy()
    env["AMODEX_CRASH_RUN_ID"] = run_id
    env["AMODEX_CRASH_AFTER_INTENT"] = "1" if after_intent else "0"
    proc = subprocess.Popen(
        [sys.executable, "-c", child],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    if not line.startswith("READY "):
        stderr = proc.stderr.read() if proc.stderr else ""
        proc.kill()
        proc.wait(timeout=5)
        raise AssertionError(f"crash worker did not become ready: {line!r} stderr={stderr!r}")
    return proc, int(line.split()[1])


def _kill_and_expire(proc: subprocess.Popen[str], old_fence: int) -> int:
    proc.kill()
    proc.wait(timeout=5)
    assert proc.returncode is not None
    time.sleep(1.2)
    return old_fence


def test_crash_before_provider_intent_is_safely_retried_once(
    repository: PostgresRunRepository,
) -> None:
    created = repository.create_run(prompt="safe retry")
    proc, old_fence = _spawn_crashing_worker(run_id=created.run_id, after_intent=False)
    old_fence = _kill_and_expire(proc, old_fence)

    provider = CountingProvider()
    replacement = Stage1Worker(
        repository=repository,
        provider=provider,
        governor=ProviderGovernor.stage1_default(),
        worker_id="replacement-worker",
    )
    completed = replacement.process_once()

    assert completed is not None
    assert completed.state == RunState.COMPLETED
    assert completed.result_text == "recovered:safe retry"
    assert provider.calls == 1
    assert completed.fencing_token > old_fence

    events = repository.list_events(created.run_id)
    assert sum(event.kind == EventKind.LEASE_EXPIRED_RECLAIMED for event in events) == 1
    assert sum(event.kind == EventKind.PROVIDER_REQUEST_STARTED for event in events) == 1
    assert sum(event.kind == EventKind.RUN_COMPLETED for event in events) == 1

    with pytest.raises(StaleLeaseError):
        repository.transition(
            run_id=created.run_id,
            worker_id="crashed-worker",
            fencing_token=old_fence,
            state=RunState.RUNNING,
            kind=EventKind.PROVIDER_ADMITTED,
        )


def test_crash_after_provider_intent_never_blindly_duplicates_call(
    repository: PostgresRunRepository,
) -> None:
    created = repository.create_run(prompt="ambiguous provider outcome")
    proc, old_fence = _spawn_crashing_worker(run_id=created.run_id, after_intent=True)
    old_fence = _kill_and_expire(proc, old_fence)

    durable_before = repository.get_run(created.run_id)
    assert durable_before is not None
    assert durable_before.provider_request_state == ProviderRequestState.STARTED
    assert durable_before.provider_request_key is not None

    provider = CountingProvider()
    replacement = Stage1Worker(
        repository=repository,
        provider=provider,
        governor=ProviderGovernor.stage1_default(),
        worker_id="replacement-worker",
    )
    recovered = replacement.process_once()

    assert recovered is not None
    assert recovered.state == RunState.UNKNOWN_OUTCOME
    assert recovered.last_error_code == "PROVIDER_OUTCOME_AMBIGUOUS"
    assert recovered.lease_owner is None
    assert recovered.fencing_token > old_fence
    assert provider.calls == 0

    events = repository.list_events(created.run_id)
    assert sum(event.kind == EventKind.PROVIDER_REQUEST_STARTED for event in events) == 1
    assert sum(event.kind == EventKind.PROVIDER_OUTCOME_UNKNOWN for event in events) == 1
    assert sum(event.kind == EventKind.RUN_COMPLETED for event in events) == 0


def test_crash_after_durable_provider_completion_finishes_without_recall(
    repository: PostgresRunRepository,
) -> None:
    created = repository.create_run(prompt="completed provider response")
    claimed = repository.claim_next(worker_id="worker-a", lease_seconds=1)
    assert claimed is not None
    running = repository.transition(
        run_id=created.run_id,
        worker_id="worker-a",
        fencing_token=claimed.fencing_token,
        state=RunState.RUNNING,
        kind=EventKind.PROVIDER_ADMITTED,
        provider="simulator",
        model="gemini-3.5-flash-lite",
    )
    request_key = new_uuid7()
    started = repository.mark_provider_request_started(
        run_id=created.run_id,
        worker_id="worker-a",
        fencing_token=running.fencing_token,
        request_key=request_key,
        provider="simulator",
        model="gemini-3.5-flash-lite",
    )
    response = DeterministicProvider(prefix="persisted").generate(
        model="gemini-3.5-flash-lite",
        prompt=started.prompt,
        request_key=request_key,
    )
    durable = repository.mark_provider_request_completed(
        run_id=created.run_id,
        worker_id="worker-a",
        fencing_token=started.fencing_token,
        request_key=request_key,
        response=response,
    )
    assert durable.provider_request_state == ProviderRequestState.COMPLETED
    assert durable.result_text == "persisted:completed provider response"

    time.sleep(1.2)

    provider = CountingProvider()
    replacement = Stage1Worker(
        repository=repository,
        provider=provider,
        governor=ProviderGovernor.stage1_default(),
        worker_id="replacement-worker",
    )
    completed = replacement.process_once()

    assert completed is not None
    assert completed.state == RunState.COMPLETED
    assert completed.result_text == "persisted:completed provider response"
    assert provider.calls == 0
    events = repository.list_events(created.run_id)
    assert sum(event.kind == EventKind.PROVIDER_REQUEST_COMPLETED for event in events) == 1
    assert sum(event.kind == EventKind.RUN_COMPLETED for event in events) == 1
