"""R2A: real loopback PostgreSQL authority, controlled faults, no inference or sleeps."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, current_thread, enumerate as threads
from urllib.parse import urlparse

import psycopg
import pytest

from amodex.contracts.ids import new_uuid7
from amodex.providers.base import ProviderFailure
from amodex.providers.governor import ProviderGovernor
from amodex.providers.simulator import DeterministicProvider
from amodex.runtime.models import EventKind, ProviderRequestState, RunState
from amodex.runtime.run_lease import RunLeasePolicy, RunLeaseRenewal
from amodex.runtime.worker import Stage1Worker
from amodex.storage.postgres import PostgresRunRepository, StaleLeaseError


@pytest.fixture
def repository():
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    target = urlparse(dsn)
    assert target.hostname in {"localhost", "127.0.0.1", "::1"}
    assert target.path.startswith("/amodex_qa_") or target.path == "/amodex_stage1"
    repo = PostgresRunRepository(dsn)
    repo.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE amodex_run_events, amodex_runs CASCADE")
    return repo


def expire(repo, run_id):
    with psycopg.connect(repo.dsn) as conn:
        conn.execute("UPDATE amodex_runs SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))


def owned(repository):
    repository.create_run(prompt="synthetic R2A")
    return repository.claim_next(worker_id="owner")


def invoke(repo, run, operation, **identity):
    args = dict(run_id=run.run_id, worker_id="owner", fencing_token=run.fencing_token)
    args.update(identity)
    if operation == "transition":
        return repo.transition(**args, state=RunState.RUNNING, kind=EventKind.PROVIDER_ADMITTED)
    if operation == "start":
        return repo.mark_provider_request_started(**args, request_key=new_uuid7(), provider="simulator", model="synthetic")
    if operation == "complete":
        response = DeterministicProvider().generate(model="synthetic", prompt="synthetic", request_key=run.provider_request_key)
        return repo.mark_provider_request_completed(**args, request_key=run.provider_request_key, response=response)
    return repo.renew_lease(**args, lease_seconds=60)


@pytest.mark.parametrize("operation", ["transition", "start", "complete", "renew"])
@pytest.mark.parametrize("fault,reason", [
    ("expired", "LEASE_EXPIRED"), ("owner", "OWNER_MISMATCH"),
    ("fence", "FENCING_MISMATCH"), ("missing", "LEASE_MISSING"),
    ("terminal", "STATE_CONFLICT"), ("takeover", "OWNER_MISMATCH"),
    ("same_owner_takeover", "FENCING_MISMATCH"),
])
def test_all_authorized_mutations_reject_lost_authority(repository, operation, fault, reason):
    run = owned(repository)
    if operation == "complete":
        run = invoke(repository, run, "start")
    identity = {}
    if fault == "expired":
        expire(repository, run.run_id)
    elif fault == "owner":
        identity["worker_id"] = "other"
    elif fault == "fence":
        identity["fencing_token"] = run.fencing_token + 1
    elif fault == "missing":
        with psycopg.connect(repository.dsn) as conn:
            conn.execute("UPDATE amodex_runs SET lease_expires_at=NULL WHERE run_id=%s", (run.run_id,))
    elif fault == "terminal":
        with psycopg.connect(repository.dsn) as conn:
            conn.execute("UPDATE amodex_runs SET state='COMPLETED' WHERE run_id=%s", (run.run_id,))
    else:
        expire(repository, run.run_id)
        assert repository.claim_next(worker_id="owner" if fault == "same_owner_takeover" else "new-owner").fencing_token == run.fencing_token + 1
    before, events = repository.get_run(run.run_id), repository.list_events(run.run_id)
    with pytest.raises(StaleLeaseError) as error:
        invoke(repository, run, operation, **identity)
    assert error.value.reason == reason
    assert repository.get_run(run.run_id) == before
    assert repository.list_events(run.run_id) == events


def test_valid_transition_and_renewal_extend_without_shortening(repository):
    run = owned(repository)
    running = invoke(repository, run, "transition")
    renewed = invoke(repository, running, "renew")
    assert renewed.lease_expires_at > running.lease_expires_at
    shorter = repository.renew_lease(run_id=run.run_id, worker_id="owner", fencing_token=run.fencing_token, lease_seconds=1)
    assert shorter.lease_expires_at == renewed.lease_expires_at
    assert shorter.fencing_token == run.fencing_token
    assert shorter.revision == renewed.revision + 1


def test_concurrent_renewals_are_sequential_and_never_reduce_expiry(repository):
    run = owned(repository)
    barrier = Barrier(2)
    def renew(seconds):
        barrier.wait(timeout=5)
        return repository.renew_lease(run_id=run.run_id, worker_id="owner", fencing_token=run.fencing_token, lease_seconds=seconds)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(renew, n) for n in (60, 120)]
        results = sorted((f.result(timeout=10) for f in futures), key=lambda r: r.revision)
    assert results[1].lease_expires_at >= results[0].lease_expires_at
    assert repository.get_run(run.run_id).lease_expires_at == results[1].lease_expires_at
    events = repository.list_events(run.run_id)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert sum(e.kind == EventKind.LEASE_RENEWED for e in events) == 2


def test_clock_is_read_after_waiting_for_row_lock(repository, monkeypatch):
    run = owned(repository)
    entered = Event()
    transaction_starts = []
    original = repository._lock_owned_run
    def observed(cur, **kwargs):
        cur.execute("SELECT NOW() AS started")
        transaction_starts.append(cur.fetchone()["started"])
        entered.set()
        return original(cur, **kwargs)
    monkeypatch.setattr(repository, "_lock_owned_run", observed)
    # Keep the row locked; expiry changes while the other transaction is active.
    with ThreadPoolExecutor(1) as pool:
        with psycopg.connect(repository.dsn) as conn:
            conn.execute("SELECT 1 FROM amodex_runs WHERE run_id=%s FOR UPDATE", (run.run_id,))
            future = pool.submit(invoke, repository, run, "transition")
            assert entered.wait(5)
            expiry = conn.execute("UPDATE amodex_runs SET lease_expires_at=clock_timestamp() WHERE run_id=%s RETURNING lease_expires_at", (run.run_id,)).fetchone()[0]
            assert expiry > transaction_starts[0]
        with pytest.raises(StaleLeaseError) as error:
            future.result(timeout=10)
    assert error.value.reason == "LEASE_EXPIRED"
    assert repository.get_run(run.run_id).revision == run.revision


def test_claim_skips_locked_rows_and_increments_fence(repository):
    first = repository.create_run(prompt="locked")
    second = repository.create_run(prompt="available")
    with psycopg.connect(repository.dsn) as conn:
        conn.execute("SELECT 1 FROM amodex_runs WHERE run_id=%s FOR UPDATE", (first.run_id,))
        claimed = repository.claim_next(worker_id="owner")
        assert claimed.run_id == second.run_id
    expire(repository, second.run_id)
    # The oldest queued Run is claimed before reclaiming the later expired one.
    assert repository.claim_next(worker_id="another").run_id == first.run_id
    reclaimed = repository.claim_next(worker_id="replacement")
    assert reclaimed.run_id == second.run_id
    assert reclaimed.fencing_token == claimed.fencing_token + 1


@pytest.mark.parametrize("operation", ["transition", "start", "complete", "renew"])
def test_event_failure_rolls_back_entire_authorized_write(repository, monkeypatch, operation):
    run = owned(repository)
    if operation == "complete":
        run = invoke(repository, run, "start")
    before, events = repository.get_run(run.run_id), repository.list_events(run.run_id)
    def fail_event(*args, **kwargs):
        raise RuntimeError("synthetic event failure")
    monkeypatch.setattr(repository, "_insert_event", fail_event)
    with pytest.raises(RuntimeError, match="synthetic event failure"):
        invoke(repository, run, operation)
    assert repository.get_run(run.run_id) == before
    assert repository.list_events(run.run_id) == events


class CountingProvider(DeterministicProvider):
    calls = 0
    def generate(self, **kwargs):
        self.calls += 1
        return super().generate(**kwargs)


def worker(repo, provider, owner="owner", policy=None):
    return Stage1Worker(repo, provider, ProviderGovernor.stage1_default(), owner, policy or RunLeasePolicy())


def assert_no_renewal_thread(run_id):
    assert not any(t.name == f"run-lease-{run_id}" for t in threads())


def test_fast_provider_completes_once_and_stops_renewal(repository):
    run = repository.create_run(prompt="synthetic")
    provider = CountingProvider()
    result = worker(repository, provider).process_once()
    assert result.state == RunState.COMPLETED
    assert result.provider_request_state == ProviderRequestState.COMPLETED
    assert provider.calls == 1
    assert len(repository.list_events(run.run_id)) == 6
    assert_no_renewal_thread(run.run_id)


@pytest.mark.parametrize("provider_failure", [False, True])
def test_expiration_during_external_call_rejects_late_response_and_never_replays(repository, provider_failure):
    run = repository.create_run(prompt="synthetic")
    class ExpiringProvider(CountingProvider):
        def generate(self, **kwargs):
            response = super().generate(**kwargs)
            expire(repository, run.run_id)
            if provider_failure:
                raise ProviderFailure(code="TIMEOUT", message="synthetic", ambiguous_outcome=True)
            return response
    provider = ExpiringProvider()
    with pytest.raises(StaleLeaseError) as error:
        worker(repository, provider).process_once()
    assert error.value.reason == "LEASE_EXPIRED"
    pending = repository.get_run(run.run_id)
    assert pending.provider_request_state == ProviderRequestState.STARTED
    assert pending.result_text is None
    assert_no_renewal_thread(run.run_id)
    result = worker(repository, provider, "replacement").process_once()
    assert result.state == RunState.UNKNOWN_OUTCOME
    assert provider.calls == 1
    assert not any(e.kind == EventKind.RUN_COMPLETED for e in repository.list_events(run.run_id))


def test_background_renewal_keeps_blocked_provider_authorized(repository, monkeypatch):
    run = repository.create_run(prompt="long synthetic operation")
    processing, renewed = Event(), Event()
    original = repository.renew_lease
    observations = []
    def observe(**kwargs):
        updated = original(**kwargs)
        if processing.is_set():
            observations.append(updated.lease_expires_at)
            renewed.set()
        return updated
    monkeypatch.setattr(repository, "renew_lease", observe)
    class BlockedProvider(CountingProvider):
        def generate(self, **kwargs):
            # Simulate elapsed execution by leaving only one second of authority.
            with psycopg.connect(repository.dsn) as conn:
                before = conn.execute("UPDATE amodex_runs SET lease_expires_at=clock_timestamp()+INTERVAL '1 second' WHERE run_id=%s RETURNING lease_expires_at", (run.run_id,)).fetchone()[0]
            processing.set()
            assert renewed.wait(5)
            assert observations[0] > before
            return super().generate(**kwargs)
    provider = BlockedProvider()
    result = worker(repository, provider, policy=RunLeasePolicy(3, 0.1)).process_once()
    assert result.state == RunState.COMPLETED and provider.calls == 1
    events = repository.list_events(run.run_id)
    assert any(e.kind == EventKind.LEASE_RENEWED for e in events)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert len({e.event_id for e in events}) == len(events)
    assert_no_renewal_thread(run.run_id)


@pytest.mark.parametrize("fault", ["connection", "database_disconnect", "expired", "takeover"])
def test_renewal_failure_during_provider_latches_loss_and_discards_response(repository, monkeypatch, caplog, fault):
    run = repository.create_run(prompt="synthetic")
    processing, renewal_finished = Event(), Event()
    original_renew = repository.renew_lease
    original_lock = repository._lock_owned_run
    def disconnect_renewal(cur, **kwargs):
        row = original_lock(cur, **kwargs)
        if current_thread().name.startswith("run-lease-"):
            with psycopg.connect(repository.dsn) as admin:
                assert admin.execute("SELECT pg_terminate_backend(%s)", (cur.connection.info.backend_pid,)).fetchone()[0]
        return row
    original_once = RunLeaseRenewal.renew_once
    def fail_renew(**kwargs):
        if not processing.is_set():
            return original_renew(**kwargs)
        if fault == "connection":
            raise psycopg.OperationalError("synthetic password=do-not-log-this")
        if fault == "database_disconnect":
            monkeypatch.setattr(repository, "_lock_owned_run", disconnect_renewal)
            return original_renew(**kwargs)
        expire(repository, run.run_id)
        if fault == "takeover":
            assert repository.claim_next(worker_id="replacement").fencing_token == kwargs["fencing_token"] + 1
        return original_renew(**kwargs)
    def signal_after_failure(self):
        original_once(self)
        if self._failure_reason is not None:
            renewal_finished.set()
    monkeypatch.setattr(repository, "renew_lease", fail_renew)
    monkeypatch.setattr(RunLeaseRenewal, "renew_once", signal_after_failure)
    class WaitingProvider(CountingProvider):
        def generate(self, **kwargs):
            processing.set()
            assert renewal_finished.wait(5)
            return super().generate(**kwargs)
    provider = WaitingProvider()
    with pytest.raises(StaleLeaseError) as error:
        worker(repository, provider, policy=RunLeasePolicy(3, 0.1)).process_once()
    assert error.value.reason == {"connection": "RENEWAL_UNCONFIRMED", "database_disconnect": "RENEWAL_UNCONFIRMED", "expired": "LEASE_EXPIRED", "takeover": "OWNER_MISMATCH"}[fault]
    assert "do-not-log-this" not in caplog.text
    assert "Run renewal stopped" in caplog.text
    pending = repository.get_run(run.run_id)
    assert pending.result_text is None
    assert pending.provider_request_state == ProviderRequestState.STARTED
    assert provider.calls == 1
    assert_no_renewal_thread(run.run_id)
    # A transport error alone does not assert expiry; only a later valid claim can reconcile.
    if fault in {"connection", "database_disconnect"}:
        assert repository.claim_next(worker_id="too-soon") is None
    expire(repository, run.run_id)
    result = worker(repository, provider, "reconciler").process_once()
    assert result.state == RunState.UNKNOWN_OUTCOME
    assert provider.calls == 1


@pytest.mark.parametrize("failure_point", ["before_completion", "completion_event", "after_completion"])
def test_external_response_persistence_faults_recover_conservatively(repository, monkeypatch, failure_point):
    run = repository.create_run(prompt="synthetic")
    provider = CountingProvider()
    original_complete, original_event = repository.mark_provider_request_completed, repository._insert_event
    def fail_completion(**kwargs):
        if failure_point == "after_completion":
            original_complete(**kwargs)
        raise RuntimeError("synthetic response persistence interruption")
    def fail_completion_event(cur, **kwargs):
        if kwargs["kind"] == EventKind.PROVIDER_REQUEST_COMPLETED:
            raise RuntimeError("synthetic response persistence interruption")
        return original_event(cur, **kwargs)
    with monkeypatch.context() as patch:
        if failure_point == "completion_event":
            patch.setattr(repository, "_insert_event", fail_completion_event)
        else:
            patch.setattr(repository, "mark_provider_request_completed", fail_completion)
        with pytest.raises(RuntimeError, match="synthetic response persistence"):
            worker(repository, provider).process_once()
    pending = repository.get_run(run.run_id)
    assert pending.provider_request_state == (ProviderRequestState.COMPLETED if failure_point == "after_completion" else ProviderRequestState.STARTED)
    expire(repository, run.run_id)
    recovered = worker(repository, provider, "replacement").process_once()
    assert recovered.state == (RunState.COMPLETED if failure_point == "after_completion" else RunState.UNKNOWN_OUTCOME)
    assert provider.calls == 1
    events = repository.list_events(run.run_id)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert sum(e.kind == EventKind.PROVIDER_REQUEST_STARTED for e in events) == 1
    assert sum(e.kind == EventKind.PROVIDER_REQUEST_COMPLETED for e in events) == (failure_point == "after_completion")
    assert_no_renewal_thread(run.run_id)


@pytest.mark.parametrize("operation", ["transition", "start", "complete", "renew"])
def test_expiry_between_lock_validation_and_update_rolls_back(repository, monkeypatch, operation):
    run = owned(repository)
    if operation == "complete":
        run = invoke(repository, run, "start")
    original_lock = repository._lock_owned_run
    events = repository.list_events(run.run_id)
    def expire_after_validation(cur, **kwargs):
        row = original_lock(cur, **kwargs)
        cur.execute("UPDATE amodex_runs SET lease_expires_at=clock_timestamp() WHERE run_id=%s", (run.run_id,))
        return row
    monkeypatch.setattr(repository, "_lock_owned_run", expire_after_validation)
    with pytest.raises(StaleLeaseError) as error:
        invoke(repository, run, operation)
    assert error.value.reason == "LEASE_EXPIRED"
    assert repository.get_run(run.run_id) == run
    assert repository.list_events(run.run_id) == events


def test_failed_renewal_before_provider_start_prevents_external_effect(repository, monkeypatch):
    run = repository.create_run(prompt="synthetic")
    provider = CountingProvider()
    original_start = repository.mark_provider_request_started
    captured = []
    original_enter = RunLeaseRenewal.__enter__
    def capture(self):
        captured.append(self)
        return original_enter(self)
    monkeypatch.setattr(RunLeaseRenewal, "__enter__", capture)
    def fail_renew(**kwargs):
        raise ConnectionError("synthetic lost connection")
    monkeypatch.setattr(repository, "renew_lease", fail_renew)
    def started_then_lose_authority(**kwargs):
        result = original_start(**kwargs)
        captured[0].renew_once()
        return result
    monkeypatch.setattr(repository, "mark_provider_request_started", started_then_lose_authority)
    with pytest.raises(StaleLeaseError):
        worker(repository, provider).process_once()
    assert provider.calls == 0
    assert repository.get_run(run.run_id).provider_request_state == ProviderRequestState.STARTED
    assert_no_renewal_thread(run.run_id)


def test_provider_observes_committed_intent_before_call(repository):
    run = repository.create_run(prompt="synthetic")
    class InspectingProvider(CountingProvider):
        def generate(self, **kwargs):
            # Separate connection observes committed STARTED before any external effect.
            pending = PostgresRunRepository(repository.dsn).get_run(run.run_id)
            assert pending.provider_request_state == ProviderRequestState.STARTED
            assert pending.provider_request_key == kwargs["request_key"]
            assert pending.result_text is None
            return super().generate(**kwargs)
    provider = InspectingProvider()
    assert worker(repository, provider).process_once().state == RunState.COMPLETED
    assert provider.calls == 1
