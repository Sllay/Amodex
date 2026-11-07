"""Real PostgreSQL contention; all dispatchers are deterministic local fakes."""
from __future__ import annotations

import os
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.environments.product import RunnerDispatchRequest, RunnerJobState
from amodex.runners.dispatch import RunnerDispatchError
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.environment import EnvironmentStateError
from test_postbaseline_environment_product import RecordingDispatcher, _create_scoped_run, environment_product_state

KEY = {"Idempotency-Key": "user-intention-1"}


@pytest.fixture(autouse=True)
def isolated_loopback_database():
    uri = urlparse(os.environ["AMODEX_TEST_DATABASE_URL"])
    assert uri.hostname in {"127.0.0.1", "localhost", "::1"} and uri.path.startswith("/amodex_qa_")


@pytest.fixture
def scope(environment_product_state):
    runs, product, envs, jobs = environment_product_state
    dispatcher = RecordingDispatcher()
    def app(dispatch=dispatcher, repository=jobs):
        return create_app(runs, product_repository=product, environment_repository=envs,
                          environment_product_repository=repository, runner_dispatcher=dispatch,
                          runner_control_token="local-r3-test-token")
    with TestClient(app(), raise_server_exceptions=False) as client:
        _, env, session, run = _create_scoped_run(client, name="R3 fixture", engine="generic",
                                                 repo_url="https://github.com/qa-local-fixture/r3")
        body = {"run_id": run["run_id"], "session_id": session["session_id"], "write_paths": ["src/**", "tests/**"]}
        yield SimpleNamespace(client=client, app=app, jobs=jobs, body=body,
            environment_id=env["environment_id"], url=f"/v1/environments/{env['environment_id']}/runner-jobs",
            dispatcher=dispatcher)


def create_pending(s, key="user-intention-1"):
    body = RunnerDispatchRequest.model_validate(s.body)
    return s.jobs.create_runner_job(environment_id=s.environment_id, idempotency_key=key,
        **body.model_dump(exclude={"schema_version"}))


def test_dispatch_ack_then_duplicate_and_new_intention(scope):
    s = scope
    first = s.client.post(s.url, json=s.body, headers=KEY)
    assert first.status_code == 201
    job = first.json()["job"]
    assert job["state"] == "DISPATCHED" and job["dispatch_status"] == "CONFIRMED"
    assert job["github_run_id"] is None  # HTTP ACK does not prove workflow execution.
    second = s.client.post(s.url, json={**s.body, "worker_profile": "implementation", "task_class": "STANDARD",
                          "write_paths": list(reversed(s.body["write_paths"]))}, headers=KEY)
    assert second.status_code == 200 and second.json()["job"]["job_id"] == job["job_id"]
    assert second.json()["job_url"] == f"/v1/runner-jobs/{job['job_id']}"
    assert len(s.dispatcher.job_ids) == 1
    fresh = s.client.post(s.url, json=s.body, headers={"Idempotency-Key": "user-intention-2"})
    assert fresh.status_code == 201 and fresh.json()["job"]["job_id"] != job["job_id"]
    assert len(s.dispatcher.job_ids) == 2


@pytest.mark.parametrize("field,value", [("worker_profile", "review"), ("task_class", "MICRO"),
    ("write_paths", ["different/**"]), ("run_id", "019a3d70-0000-7000-8000-000000000001"),
    ("session_id", "019a3d70-0000-7000-8000-000000000002")])
def test_same_key_different_payload_is_409(scope, field, value):
    s = scope
    assert s.client.post(s.url, json=s.body, headers=KEY).status_code == 201
    other = s.client.post(s.url, json={**s.body, field: value}, headers=KEY)
    assert other.status_code == 409 and other.json()["detail"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert len(s.jobs.list_runner_jobs()) == 1 and len(s.dispatcher.job_ids) == 1


@pytest.mark.parametrize("key", [None, "", "a b", "bad/key", "x"*129])
def test_missing_or_invalid_key_is_rejected_before_creation(scope, key):
    headers = {} if key is None else {"Idempotency-Key": key}
    assert scope.client.post(scope.url, json=scope.body, headers=headers).status_code == 422
    assert not scope.jobs.list_runner_jobs() and not scope.dispatcher.job_ids


@pytest.mark.parametrize("ambiguous", [False, True])
@pytest.mark.parametrize("phase", ["CLAIMED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"])
def test_claim_progress_or_terminal_before_ack_never_regresses(scope, phase, ambiguous):
    s = scope
    observed = []
    def dispatch(*, job_id):
        # Independent connection proves intent/STARTED were committed and no row
        # lock remains held while external IO happens.
        with psycopg.connect(s.jobs.dsn) as conn:
            row = conn.execute("SELECT dispatch_status,dispatch_attempt_id,dispatch_started_at,dispatch_target FROM amodex_environment_runner_jobs WHERE job_id=%s FOR UPDATE NOWAIT", (job_id,)).fetchone()
            assert row[0] == "STARTED" and all(x is not None for x in row[1:])
        s.jobs.claim_runner_job(job_id=job_id, github_run_id="fast-owner")
        if phase != "CLAIMED": s.jobs.mark_running(job_id=job_id, github_run_id="fast-owner")
        if phase not in {"CLAIMED", "RUNNING"}:
            s.jobs.report_runner_job(job_id=job_id, github_run_id="fast-owner", state=RunnerJobState(phase),
                                     summary="progress must survive", result_commit="a"*40)
        observed.append(s.jobs.get_runner_job(job_id))
        if ambiguous: raise RunnerDispatchError("synthetic timeout after possible acceptance")
    s.dispatcher.dispatch = dispatch
    response = s.client.post(s.url, json=s.body, headers=KEY)
    assert response.status_code == (202 if ambiguous else 201)
    current = s.jobs.get_runner_job(response.json()["job"]["job_id"])
    assert current.state.value == phase
    for field in ("github_run_id", "claimed_at", "lease_expires_at", "last_heartbeat_at", "heartbeat_count",
                  "completed_at", "summary", "result_commit"):
        assert getattr(current, field) == getattr(observed[0], field)
    assert current.dispatch_status == ("UNKNOWN" if ambiguous else "CONFIRMED")
    assert s.client.post(s.url, json=s.body, headers=KEY).json()["job"]["state"] == phase


def test_two_identical_posts_overlap_during_sending_and_only_one_dispatches(scope):
    s = scope
    started, release = Event(), Event()
    calls = []
    def dispatch(*, job_id):
        calls.append(job_id)
        started.set()
        assert release.wait(8)
    s.dispatcher.dispatch = dispatch
    def post():
        with TestClient(s.app(repository=PostgresEnvironmentProductRepository(s.jobs.dsn))) as client:
            return client.post(s.url, json=s.body, headers=KEY)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(post)
        try:
            assert started.wait(8)
            repeated = pool.submit(post).result(timeout=8)
            assert repeated.status_code == 202
            assert repeated.json()["job"]["dispatch_status"] == "STARTED"
            assert not repeated.json()["dispatch_operation"]["automatic_redispatch"]
        finally:
            release.set()
        completed = first.result(timeout=8)
    assert completed.status_code == 201 and len(calls) == 1
    assert repeated.json()["job"]["job_id"] == completed.json()["job"]["job_id"]
    assert len(s.jobs.list_runner_jobs()) == 1


def test_simultaneous_intake_transactions_have_one_job(scope):
    s = scope
    barrier = Barrier(2)
    def intake(_):
        barrier.wait(timeout=8)
        repo = PostgresEnvironmentProductRepository(s.jobs.dsn)
        return repo.create_runner_job(environment_id=s.environment_id, idempotency_key="simultaneous",
            **RunnerDispatchRequest.model_validate(s.body).model_dump(exclude={"schema_version"}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(intake, range(2)))
    assert jobs[0].job_id == jobs[1].job_id
    assert jobs[0].dispatch_attempt_id == jobs[1].dispatch_attempt_id
    assert len(s.jobs.list_runner_jobs()) == 1


def test_different_payloads_competing_for_same_key_have_one_conflict(scope):
    s = scope
    barrier = Barrier(2)
    def intake(profile):
        barrier.wait(timeout=8)
        try:
            return s.jobs.create_runner_job(environment_id=s.environment_id, idempotency_key="competing-payloads",
                **RunnerDispatchRequest.model_validate({**s.body, "worker_profile": profile}).model_dump(exclude={"schema_version"})).job_id
        except EnvironmentStateError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(intake, ["implementation", "review"]))
    assert outcomes.count("IDEMPOTENCY_KEY_CONFLICT") == 1
    assert len(s.jobs.list_runner_jobs()) == 1


def test_two_controllers_can_acquire_only_one_sending_authorization(scope):
    job = create_pending(scope)
    barrier = Barrier(2)
    def begin(controller):
        barrier.wait(timeout=8)
        return PostgresEnvironmentProductRepository(scope.jobs.dsn).begin_dispatch(
            job_id=job.job_id, target={"controller": controller})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(begin, ["a", "b"]))
    assert sum(r is not None for r in results) == 1
    assert scope.jobs.get_runner_job(job.job_id).dispatch_status == "STARTED"


def test_pending_intent_survives_restart_and_can_acquire_first_send(scope):
    s = scope
    job = create_pending(s)  # Simulate crash after intent commit, before acquiring send.
    with TestClient(s.app(repository=PostgresEnvironmentProductRepository(s.jobs.dsn))) as fresh:
        response = fresh.post(s.url, json=s.body, headers=KEY)
    assert response.status_code == 201 and response.json()["job"]["job_id"] == job.job_id
    assert s.dispatcher.job_ids == [job.job_id]


def test_dispatch_unavailable_keeps_intent_pending_for_the_first_send(scope, monkeypatch):
    s = scope
    monkeypatch.delenv("AMODEX_GITHUB_DISPATCH_REPOSITORY", raising=False)
    monkeypatch.delenv("AMODEX_GITHUB_DISPATCH_TOKEN", raising=False)
    with TestClient(s.app(dispatch=None)) as unconfigured:
        pending = unconfigured.post(s.url, json=s.body, headers=KEY)
    assert pending.status_code == 202 and pending.json()["dispatch"] == "DISPATCH_UNAVAILABLE"
    assert pending.json()["job"]["dispatch_status"] == "PENDING"
    assert not pending.json()["dispatch_operation"]["may_have_been_accepted"]
    sent = s.client.post(s.url, json=s.body, headers=KEY)
    assert sent.status_code == 201
    assert sent.json()["job"]["job_id"] == pending.json()["job"]["job_id"]
    assert s.dispatcher.job_ids == [sent.json()["job"]["job_id"]]


@pytest.mark.parametrize("accepted", [False, True])
def test_crash_after_start_or_acceptance_never_replays_on_restart(scope, accepted, monkeypatch):
    s = scope
    if accepted:
        def lost_ack(**kwargs): raise RuntimeError("synthetic process failure before ACK commit")
        monkeypatch.setattr(s.jobs, "mark_dispatched", lost_ack)
        assert s.client.post(s.url, json=s.body, headers=KEY).status_code == 500
        job = s.jobs.list_runner_jobs()[0]
        assert s.dispatcher.job_ids == [job.job_id]
    else:
        job = create_pending(s)
        assert s.jobs.begin_dispatch(job_id=job.job_id, target={"repository": "fixture/local"})
    fresh_repo = PostgresEnvironmentProductRepository(s.jobs.dsn)
    replacement = RecordingDispatcher()
    with TestClient(s.app(dispatch=replacement, repository=fresh_repo)) as fresh:
        response = fresh.post(s.url, json=s.body, headers=KEY)
        queried = fresh.get(response.json()["job_url"])
    assert response.status_code == 202 and queried.status_code == 200
    assert response.json()["dispatch_operation"]["may_have_been_accepted"]
    assert queried.json()["dispatch_status"] == "STARTED"
    assert queried.json()["job_id"] == job.job_id
    assert not replacement.job_ids


def test_timeout_after_possible_acceptance_stays_unknown_across_restart(scope):
    s = scope
    calls = []
    def dispatch(*, job_id):
        calls.append(job_id)
        raise RunnerDispatchError("synthetic connection lost after possible acceptance")
    s.dispatcher.dispatch = dispatch
    response = s.client.post(s.url, json=s.body, headers=KEY)
    assert response.status_code == 202
    assert response.json()["job"]["state"] == "DISPATCH_UNKNOWN"
    assert response.json()["job"]["dispatch_status"] == "UNKNOWN"
    replacement = RecordingDispatcher()
    with TestClient(s.app(dispatch=replacement, repository=PostgresEnvironmentProductRepository(s.jobs.dsn))) as fresh:
        for _ in range(2):
            again = fresh.post(s.url, json=s.body, headers=KEY)
            assert again.status_code == 202
            assert again.json()["job"]["job_id"] == calls[0]
    assert len(calls) == 1 and not replacement.job_ids


def test_new_process_recognizes_original_intent_and_cannot_reacquire(scope):
    s = scope
    job = create_pending(s)
    s.jobs.begin_dispatch(job_id=job.job_id, target={"adapter": "fake"})
    s.jobs.mark_dispatch_unknown(job_id=job.job_id, attempt_id=job.dispatch_attempt_id)
    code = '''
import os,sys,json
from amodex.environments.product import RunnerDispatchRequest
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
p=json.loads(sys.stdin.read())
r=PostgresEnvironmentProductRepository(os.environ["AMODEX_TEST_DATABASE_URL"])
j=r.create_runner_job(environment_id=p['environment'],idempotency_key='user-intention-1',
    **RunnerDispatchRequest.model_validate(p['body']).model_dump(exclude={'schema_version'}))
print(json.dumps({'job_id':j.job_id,'status':j.dispatch_status,'acquired':r.begin_dispatch(job_id=j.job_id,target={}) is not None}))
'''
    result = subprocess.run([sys.executable, "-c", code], input=json.dumps({"environment": s.environment_id, "body": s.body}),
                            text=True, capture_output=True, check=True, timeout=10)
    assert json.loads(result.stdout) == {"job_id": job.job_id, "status": "UNKNOWN", "acquired": False}


def test_key_namespace_is_per_environment(scope):
    s = scope
    first = s.client.post(s.url, json=s.body, headers=KEY)
    _, env, session, run = _create_scoped_run(s.client, name="second R3 environment", engine="generic",
                                             repo_url="https://github.com/qa-local-fixture/second")
    second = s.client.post(f"/v1/environments/{env['environment_id']}/runner-jobs", headers=KEY,
                           json={"run_id": run["run_id"], "session_id": session["session_id"]})
    assert first.status_code == second.status_code == 201
    assert first.json()["job"]["job_id"] != second.json()["job"]["job_id"]


def test_migration_is_reentrant_and_legacy_rows_remain_readable_not_sendable(scope):
    s = scope
    job = create_pending(s)
    with psycopg.connect(s.jobs.dsn) as conn:
        # Represents an existing pre-R3 row.
        conn.execute("UPDATE amodex_environment_runner_jobs SET idempotency_key=NULL,intent_digest=NULL,dispatch_attempt_id=NULL,dispatch_status=NULL WHERE job_id=%s", (job.job_id,))
        conn.execute(Path("migrations/postbaseline/0008_runner_dispatch_idempotency.sql").read_text())
    legacy = s.jobs.get_runner_job(job.job_id)
    assert legacy.job_id == job.job_id and legacy.dispatch_status is None
    assert s.jobs.begin_dispatch(job_id=job.job_id, target={"adapter": "fake"}) is None


def test_mismatched_dispatch_receipt_does_not_change_state(scope):
    job = create_pending(scope)
    scope.jobs.begin_dispatch(job_id=job.job_id, target={"adapter": "fake"})
    with pytest.raises(EnvironmentStateError, match="DISPATCH_ATTEMPT_MISMATCH"):
        scope.jobs.mark_dispatched(job_id=job.job_id, attempt_id="019a3d70-0000-7000-8000-000000000001")
    assert scope.jobs.get_runner_job(job.job_id).dispatch_status == "STARTED"


def test_pending_job_does_not_send_after_scope_becomes_inactive(scope):
    s = scope
    job = create_pending(s)
    with psycopg.connect(s.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_sessions SET status='ARCHIVED' WHERE session_id=%s", (job.session_id,))
    response = s.client.post(s.url, json=s.body, headers=KEY)
    assert response.status_code == 202 and response.json()["job"]["dispatch_status"] == "PENDING"
    assert not s.dispatcher.job_ids
