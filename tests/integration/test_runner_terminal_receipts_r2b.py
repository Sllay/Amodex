"""Transactional terminal proof on real loopback PostgreSQL; no external work."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from urllib.parse import urlparse

from fastapi.testclient import TestClient
import httpx
import psycopg
import pytest

from amodex.contracts.ids import new_uuid7
from amodex.environments.product import RunnerReportRequest
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.product import PostgresProductRepository
from amodex.runners.client import RunnerControlClient
from amodex.runners.supervisor import RunnerSupervisor
from test_postbaseline_environment_product import environment_product_state
from test_runner_dispatch_idempotency_r3 import scope, KEY

AUTH = {"authorization": "Bearer local-r3-test-token"}


@pytest.fixture(scope="session", autouse=True)
def local_terminal_database_only():
    target = urlparse(os.environ["AMODEX_TEST_DATABASE_URL"])
    assert target.hostname in {"127.0.0.1", "localhost", "::1"}
    assert target.path.startswith("/amodex_qa_")


@pytest.fixture
def terminal(scope):
    s = scope
    s.job = s.client.post(s.url, json=s.body, headers=KEY).json()["job"]["job_id"]
    claimed = s.jobs.claim_runner_job(job_id=s.job, github_run_id="owner")["job"]
    s.attempt = claimed.execution_attempt_id
    s.report = {"github_run_id": "owner", "attempt_id": s.attempt, "state": "COMPLETED",
                "summary": "Pure local validation", "result_commit": "a"*40,
                "evidence": {"checks": 3, "score": 42, "references": ["artifact://tests/result.json"]}}
    s.endpoint = f"/v1/runner-jobs/{s.job}/report"
    s.query = f"/v1/runner-jobs/{s.job}/terminal-receipt"
    s.query_args = {"github_run_id": "owner", "attempt_id": s.attempt,
                    "report_digest": RunnerReportRequest(**s.report).digest(s.job)}
    return s


def post(s, body=None):
    return s.client.post(s.endpoint, json=body or s.report, headers=AUTH)


def count(s):
    with psycopg.connect(s.jobs.dsn) as conn:
        receipts = conn.execute("SELECT count(*) FROM amodex_runner_terminal_receipts WHERE job_id=%s", (s.job,)).fetchone()[0]
        audits = conn.execute("SELECT count(*) FROM amodex_audit_events WHERE subject_id=%s AND action IN ('RUNNER_JOB_COMPLETED','RUNNER_JOB_FAILED','RUNNER_JOB_CANCELLED')", (s.job,)).fetchone()[0]
    return receipts, audits


def test_first_report_commits_proof_atomically_and_does_not_finish_run(terminal):
    s = terminal
    before = s.jobs.get_runner_job(s.job)
    response = post(s)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["attempt_id"] == s.attempt and receipt["report_digest"] == s.query_args["report_digest"]
    current = s.jobs.get_runner_job(s.job)
    assert current.state.value == "COMPLETED" and current.terminal_receipt_id == receipt["receipt_id"]
    assert current.lease_expires_at is None and count(s) == (1, 1)
    with psycopg.connect(s.jobs.dsn) as conn:
        run = conn.execute("SELECT state FROM amodex_runs WHERE run_id=%s", (before.run_id,)).fetchone()[0]
    assert run == "QUEUED"
    same = post(s)
    assert same.status_code == 200 and same.json() == receipt
    assert s.jobs.get_runner_job(s.job) == current and count(s) == (1, 1)


@pytest.mark.parametrize("field,value", [("summary", "different"), ("state", "FAILED"),
    ("result_commit", "b"*40), ("evidence", {"score": 99}), ("attempt_id", "019a3d70-0000-7000-8000-000000000001"),
    ("github_run_id", "other-owner")])
def test_divergent_reports_or_identity_are_refused(terminal, field, value):
    s = terminal
    receipt = post(s).json()
    conflict = post(s, {**s.report, field:value})
    assert conflict.status_code == 409
    assert count(s) == (1, 1) and post(s).json() == receipt


def test_expired_before_commit_cannot_conclude_but_expired_after_commit_can_read(terminal):
    s = terminal
    with psycopg.connect(s.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE job_id=%s", (s.job,))
    assert post(s).status_code == 409 and count(s) == (0, 0)
    with psycopg.connect(s.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET lease_expires_at=clock_timestamp()+INTERVAL '2 minutes' WHERE job_id=%s", (s.job,))
    receipt = post(s).json()
    with psycopg.connect(s.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE job_id=%s", (s.job,))
    current = s.jobs.get_runner_job(s.job)
    assert post(s).json() == receipt
    response = s.client.get(s.query, params=s.query_args, headers=AUTH)
    assert response.status_code == 200 and response.json() == receipt
    assert s.jobs.get_runner_job(s.job) == current and count(s) == (1, 1)


@pytest.mark.parametrize("where", ["audit", "receipt"])
def test_any_write_failure_rolls_back_job_audit_and_receipt(terminal, monkeypatch, where):
    s = terminal
    before = s.jobs.get_runner_job(s.job)
    def fail(*args, **kwargs): raise RuntimeError("synthetic transaction write failure")
    if where == "audit": monkeypatch.setattr(PostgresProductRepository, "_insert_audit", fail)
    else: monkeypatch.setattr(s.jobs, "_insert_terminal_receipt", fail)
    assert post(s).status_code == 500
    assert s.jobs.get_runner_job(s.job) == before and count(s) == (0, 0)


def test_lease_expiring_during_transaction_is_checked_at_commit(terminal, monkeypatch):
    s = terminal
    with psycopg.connect(s.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET lease_expires_at=clock_timestamp()+INTERVAL '.3 seconds' WHERE job_id=%s", (s.job,))
    original = PostgresProductRepository._insert_audit
    def slow(cur, **kwargs):
        cur.execute("SELECT pg_sleep(.4)")
        return original(cur, **kwargs)
    monkeypatch.setattr(PostgresProductRepository, "_insert_audit", staticmethod(slow))
    response = post(s)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RUNNER_LEASE_LOST"
    assert s.jobs.get_runner_job(s.job).state.value == "CLAIMED" and count(s) == (0, 0)


def test_response_lost_after_commit_is_recognized_once(terminal, monkeypatch):
    s = terminal
    original = s.jobs.report_runner_job
    def lose(**kwargs):
        original(**kwargs)
        raise RuntimeError("synthetic crash before HTTP response")
    monkeypatch.setattr(s.jobs, "report_runner_job", lose)
    assert post(s).status_code == 500
    assert count(s) == (1, 1)
    monkeypatch.setattr(s.jobs, "report_runner_job", original)
    recognized = post(s)
    assert recognized.status_code == 200 and count(s) == (1, 1)
    assert s.client.get(s.query, params=s.query_args, headers=AUTH).json() == recognized.json()


@pytest.mark.parametrize("different", [False, True])
def test_simultaneous_reports_have_one_terminal_audit_and_receipt(terminal, different):
    s = terminal
    barrier = Barrier(2)
    def submit(index):
        with TestClient(s.app(repository=PostgresEnvironmentProductRepository(s.jobs.dsn))) as client:
            barrier.wait(timeout=5)
            body = {**s.report, "summary": "other"} if different and index else s.report
            return client.post(s.endpoint, json=body, headers=AUTH)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert sorted(r.status_code for r in responses) == ([200,409] if different else [200,200])
    if not different: assert responses[0].json() == responses[1].json()
    assert count(s) == (1, 1)


def test_receipt_survives_service_restart_and_a_new_process(terminal):
    s = terminal
    receipt = post(s).json()
    code = '''import os,sys,json
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
p=json.loads(sys.stdin.read())
r=PostgresEnvironmentProductRepository(os.environ['AMODEX_TEST_DATABASE_URL'])
print(r.get_terminal_receipt(**p).model_dump_json())
'''
    result = subprocess.run([sys.executable,"-c",code],input=json.dumps({"job_id":s.job,**s.query_args}),
        text=True,capture_output=True,check=True,timeout=10)
    assert json.loads(result.stdout) == receipt


def test_old_attempt_with_reused_workflow_owner_cannot_mutate_current_attempt(terminal):
    s = terminal
    replacement = new_uuid7()
    with psycopg.connect(s.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET execution_attempt_id=%s WHERE job_id=%s", (replacement,s.job))
    response = post(s)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RUNNER_ATTEMPT_MISMATCH"
    assert count(s) == (0, 0)
    accepted = post(s,{**s.report,"attempt_id":replacement})
    assert accepted.status_code == 200
    assert post(s).status_code == 409 and count(s) == (1, 1)


def test_legacy_terminal_has_no_fabricated_receipt_or_new_authority(terminal):
    s = terminal
    with psycopg.connect(s.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET execution_attempt_id=NULL,state='COMPLETED',lease_expires_at=NULL WHERE job_id=%s", (s.job,))
        conn.execute(Path("migrations/postbaseline/0009_runner_terminal_receipts.sql").read_text())
    assert post(s).status_code == 409
    assert s.client.get(s.query, params=s.query_args, headers=AUTH).status_code == 404
    assert count(s) == (0, 0)


def test_database_rejects_unreceipted_new_terminal(terminal):
    s = terminal
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(s.jobs.dsn) as conn:
            conn.execute("UPDATE amodex_environment_runner_jobs SET state='COMPLETED' WHERE job_id=%s", (s.job,))
    assert s.jobs.get_runner_job(s.job).state.value == "CLAIMED"


def test_receipt_query_requires_machine_auth_and_exact_identity(terminal):
    s = terminal
    assert post(s).status_code == 200
    assert s.client.get(s.query, params=s.query_args).status_code == 401
    for key,value in (("attempt_id",new_uuid7()),("github_run_id","wrong"),("report_digest","f"*64)):
        assert s.client.get(s.query, params={**s.query_args,key:value}, headers=AUTH).status_code == 409


def test_sanitized_evidence_and_summary_do_not_collapse_conflicting_intent(terminal):
    s = terminal
    assert post(s,{**s.report,"evidence":{"token":"never-store"}}).status_code == 422
    body = {**s.report,"summary":"Token=must-not-be-stored https://user:pw@example.invalid"}
    response = post(s,body)
    assert response.status_code == 200 and "must-not-be-stored" not in response.text and "example.invalid" not in response.text
    assert post(s,{**body,"summary":"Token=different-private-value"}).status_code == 409
    assert count(s) == (1, 1)


def test_supervisor_reconciles_unknown_report_read_only_without_new_work(terminal, monkeypatch, tmp_path):
    s = terminal
    calls = []
    lose = True
    def transport(method,url,**kwargs):
        calls.append((method,url.rsplit('/',1)[-1]))
        response = s.client.request(method,url,**{k:kwargs[k] for k in ('headers','params','json') if k in kwargs})
        if url.endswith('/report') and lose: raise httpx.ReadTimeout('response lost')
        return response
    monkeypatch.setattr(httpx,'request',transport)
    client=RunnerControlClient('http://testserver','local-r3-test-token')
    def supervisor(): return RunnerSupervisor(client=client,job_id=s.job,github_run_id='owner',state_file=tmp_path/'s.json')
    with supervisor() as runner:
        result=runner.report(state='COMPLETED',summary=s.report['summary'],result_commit=s.report['result_commit'],evidence=s.report['evidence'])
        assert result.status=='REPORT_UNKNOWN' and count(s)==(1,1)
    calls.clear()
    with supervisor() as runner:
        assert runner.reconcile_receipt().status=='RECONCILED'
        assert runner.run([sys.executable,'-c','raise Exception("must not run")'],operation='retry-provider').status=='BLOCKED'
        assert runner.report(state='FAILED',summary='fallback').status=='REPORT_BLOCKED'
    assert calls==[('GET','terminal-receipt')] and count(s)==(1,1)


def test_no_receipt_and_lost_authority_stays_unknown_without_post_retry(terminal, monkeypatch, tmp_path):
    s = terminal
    calls = []
    def transport(method,url,**kwargs):
        calls.append((method,url.rsplit('/',1)[-1]))
        if url.endswith('/report'): raise httpx.ReadTimeout('not known whether accepted')
        return s.client.request(method,url,**{k:kwargs[k] for k in ('headers','params','json') if k in kwargs})
    monkeypatch.setattr(httpx,'request',transport)
    client=RunnerControlClient('http://testserver','local-r3-test-token')
    def supervisor(): return RunnerSupervisor(client=client,job_id=s.job,github_run_id='owner',state_file=tmp_path/'state')
    with supervisor() as runner:
        assert runner.report(state='COMPLETED',summary='done').status=='REPORT_UNKNOWN'
    with psycopg.connect(s.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE job_id=%s",(s.job,))
    calls.clear()
    with supervisor() as runner:
        assert runner.reconcile_receipt().status=='UNKNOWN_OUTCOME'
        assert runner.report(state='FAILED',summary='fallback').status=='REPORT_BLOCKED'
        assert runner.run([sys.executable,'-c','pass'],operation='retry-dispatch').status=='BLOCKED'
    assert calls==[('GET','terminal-receipt')] and count(s)==(0,0)


def test_attempt_change_does_not_grant_new_authority_to_supervisor(terminal, monkeypatch, tmp_path):
    s = terminal
    def transport(method,url,**kwargs):
        return s.client.request(method,url,**{k:kwargs[k] for k in ('headers','params','json') if k in kwargs})
    monkeypatch.setattr(httpx,'request',transport)
    client=RunnerControlClient('http://testserver','local-r3-test-token')
    marker=tmp_path/'forbidden'
    with RunnerSupervisor(client=client,job_id=s.job,github_run_id='owner',state_file=tmp_path/'state') as runner:
        assert runner.running().status=='COMPLETE'
        with psycopg.connect(s.jobs.dsn) as conn:
            conn.execute("UPDATE amodex_environment_runner_jobs SET execution_attempt_id=%s WHERE job_id=%s",(new_uuid7(),s.job))
        result=runner.run([sys.executable,'-c',f'open({str(marker)!r},"w").write("bad")'],operation='old-attempt')
        assert result.status=='BLOCKED' and result.authority=='LEASE_LOST'
        assert runner.state['attempt_id']==s.attempt
    assert not marker.exists() and count(s)==(0,0)


def test_audit_required_by_receipt_cannot_be_deleted_independently(terminal):
    s=terminal
    receipt=post(s).json()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(s.jobs.dsn) as conn:
            conn.execute('DELETE FROM amodex_audit_events WHERE audit_id=%s',(receipt['audit_id'],))
    assert count(s)==(1,1)
