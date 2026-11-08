"""Real R1 middleware/ownership/SQL under a local HTTP transport, never dispatch."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import httpx
import psycopg
import pytest

from amodex.runners.client import AuthorityStatus, RunnerClientError, RunnerControlClient
from amodex.runners.supervisor import RunnerSupervisor
from amodex.environments.product import RunnerJobState
from test_postbaseline_environment_product import environment_product_state
from test_runner_dispatch_idempotency_r3 import KEY, scope


@pytest.fixture
def owned(scope, monkeypatch, tmp_path):
    s = scope
    response = s.client.post(s.url, json=s.body, headers=KEY)
    job = response.json()["job"]["job_id"]
    s.jobs.claim_runner_job(job_id=job, github_run_id="owner")
    calls = []
    def request(method, url, **kwargs):
        calls.append(url.rsplit("/", 1)[-1])
        return s.client.request(method, url, **{k: kwargs[k] for k in ("headers", "json", "params") if k in kwargs})
    monkeypatch.setattr(httpx, "request", request)
    s.job, s.calls = job, calls
    s.control = RunnerControlClient("http://testserver", "local-r3-test-token")
    s.supervisor = lambda client=s.control: RunnerSupervisor(client=client, job_id=job, github_run_id="owner",
        state_file=tmp_path/"state.json", interval_seconds=.04, grace_seconds=.1, control_timeout_seconds=2)
    return s


def expire(s):
    with psycopg.connect(s.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE job_id=%s", (s.job,))


@pytest.mark.parametrize("fault", ["expiry", "owner", "auth", "terminal"])
def test_server_rejection_prevents_launch_and_late_report(owned, tmp_path, fault):
    s = owned
    expected = AuthorityStatus.LEASE_LOST
    if fault == "expiry": expire(s)
    elif fault == "owner":
        with psycopg.connect(s.jobs.dsn) as conn:
            conn.execute("UPDATE amodex_environment_runner_jobs SET github_run_id='other' WHERE job_id=%s", (s.job,))
    elif fault == "terminal":
        s.jobs.report_runner_job(job_id=s.job, github_run_id="owner", state=RunnerJobState.COMPLETED, summary="already done", result_commit=None)
    else:
        s.control = RunnerControlClient("http://testserver", "incorrect-synthetic-token")
        expected = AuthorityStatus.AUTH_REJECTED
    marker = tmp_path/"unauthorized-work"
    before = s.jobs.get_runner_job(s.job)
    with s.supervisor(client=s.control) as supervisor:
        result = supervisor.run([sys.executable, "-c", f"open({str(marker)!r},'w').write('unsafe')"], operation="tool")
        assert result.status == "BLOCKED" and result.authority == expected
        assert supervisor.report(state="COMPLETED", summary="late success").status == "REPORT_BLOCKED"
        assert supervisor.report(state="FAILED", summary="late failure").status == "REPORT_BLOCKED"
    assert not marker.exists() and s.calls == ["heartbeat"]
    assert s.jobs.get_runner_job(s.job).state == before.state


def test_expiry_during_active_process_stops_and_server_still_rejects_late_report(owned, tmp_path, monkeypatch):
    s = owned
    original = RunnerControlClient.check_authority
    count = 0
    def renew(self, **kwargs):
        nonlocal count
        result = original(self, **kwargs)
        count += 1
        if count == 1: expire(s)
        return result
    monkeypatch.setattr(RunnerControlClient, "check_authority", renew)
    marker = tmp_path/"should-not-finish"
    with s.supervisor() as supervisor:
        result = supervisor.run([sys.executable, "-c", f"import time; time.sleep(30); open({str(marker)!r},'w').write('unsafe')"], operation="tool")
        assert result.status == "BLOCKED" and result.authority == "LEASE_LOST"
        assert supervisor.report(state="FAILED", summary="late").status == "REPORT_BLOCKED"
    assert not marker.exists() and "report" not in s.calls
    with pytest.raises(RunnerClientError) as rejected:
        s.control.report(job_id=s.job, github_run_id="owner", state="COMPLETED", summary="bypass attempt")
    assert rejected.value.status == AuthorityStatus.LEASE_LOST
    assert s.jobs.get_runner_job(s.job).state.value == "CLAIMED"


def test_controlled_cancel_reports_once_with_a_fresh_valid_lease(owned, monkeypatch):
    s = owned
    original, calls = RunnerControlClient.check_authority, 0
    def renew(self, **kwargs):
        nonlocal calls
        result = original(self, **kwargs)
        calls += 1
        if calls == 2: supervisor.cancel()
        return result
    monkeypatch.setattr(RunnerControlClient, "check_authority", renew)
    with s.supervisor() as supervisor:
        result = supervisor.run([sys.executable, "-c", "import time; time.sleep(30)"], operation="work")
        assert result.status == "CANCELLED"
        assert supervisor.report(state="CANCELLED", summary="operator cancelled").status == "REPORTED"
        assert supervisor.report(state="CANCELLED", summary="retry").status == "REPORT_BLOCKED"
    job = s.jobs.get_runner_job(s.job)
    assert job.state.value == "CANCELLED" and job.lease_expires_at is None and job.github_run_id == "owner"
    assert s.calls.count("report") == 1


def test_lost_terminal_ack_never_sends_a_second_report(owned, monkeypatch):
    s = owned
    original = httpx.request
    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if url.endswith("/report"): raise httpx.ReadTimeout("private raw response")
        return response
    monkeypatch.setattr(httpx, "request", request)
    with s.supervisor() as supervisor:
        assert supervisor.run([sys.executable, "-c", "pass"], operation="work").status == "COMPLETE"
        assert supervisor.report(state="COMPLETED", summary="done").status == "REPORT_UNKNOWN"
    with s.supervisor() as supervisor:
        assert supervisor.report(state="FAILED", summary="fallback").status == "REPORT_BLOCKED"
    assert s.jobs.get_runner_job(s.job).state.value == "COMPLETED"
    assert s.calls.count("report") == 1
