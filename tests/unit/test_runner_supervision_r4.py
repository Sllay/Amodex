"""Local process groups and deterministic control replies; no external services."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import httpx
import pytest

from amodex.runners.client import AuthorityStatus as A, HeartbeatResult, RunnerControlClient
from amodex.runners.supervisor import RunnerSupervisor
from amodex.environments.product import RunnerReportRequest, RunnerTerminalReceipt

JOB = "019a3d70-0000-7000-8000-000000000001"
ATTEMPT = "019a3d70-0000-7000-8000-000000000002"


class Control:
    def __init__(self, replies=None, on_heartbeat=None):
        self.replies = list(replies or [])
        self.beats = 0
        self.reports = []
        self.on_heartbeat = on_heartbeat

    def check_authority(self, **kwargs):
        self.beats += 1
        if self.on_heartbeat: self.on_heartbeat(self.beats)
        status = self.replies.pop(0) if self.replies else A.HEALTHY
        return HeartbeatResult(status, lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=60), attempt_id=ATTEMPT)

    def running(self, **kwargs):
        return {**kwargs, "state": "RUNNING"}

    def report(self, **kwargs):
        self.reports.append(kwargs)
        job = kwargs['job_id']
        body = RunnerReportRequest(**{k:v for k,v in kwargs.items() if k!='job_id'})
        return RunnerTerminalReceipt(receipt_id=JOB,job_id=job,attempt_id=body.attempt_id,
            github_run_id=body.github_run_id,report_digest=body.digest(job),state=body.state,
            summary=body.summary,result_commit=body.result_commit,evidence=body.evidence,
            audit_id=JOB,environment_id=JOB,session_id=JOB,run_id=JOB,committed_at=datetime.now(timezone.utc)).model_dump(mode='json')


def supervisor(tmp_path, control=None, **kwargs):
    return RunnerSupervisor(client=control or Control(), job_id=JOB, github_run_id="owner",
        state_file=tmp_path/"supervision.json", interval_seconds=0.03, grace_seconds=0.08,
        control_timeout_seconds=0.2, **kwargs)


def python(source):
    return [sys.executable, "-c", source]


def test_healthy_heartbeat_during_work_and_no_heartbeat_after_completion(tmp_path):
    control = Control()
    with supervisor(tmp_path, control) as s:
        result = s.run(python("import time; time.sleep(.16)"), operation="validate")
        assert result.status == "COMPLETE" and control.beats >= 3
    count = control.beats
    time.sleep(0.04)
    assert control.beats == count
    assert json.loads((tmp_path/"supervision.json").read_text())["operations"]["validate"] == "COMPLETE"


@pytest.mark.parametrize("status,code,expected", [
    (200, None, A.HEALTHY), (409, "RUNNER_LEASE_LOST", A.LEASE_LOST),
    (409, "RUNNER_LEASE_OWNER_MISMATCH", A.LEASE_LOST),
    (409, "RUNNER_JOB_STATE_CONFLICT", A.LEASE_LOST),
    (409, "new-unknown-code", A.AUTHORITY_UNCONFIRMED),
    (401, None, A.AUTH_REJECTED), (403, None, A.AUTH_REJECTED),
    (500, None, A.CONTROL_PLANE_ERROR), (503, None, A.CONTROL_PLANE_ERROR),
    (202, None, A.AUTHORITY_UNCONFIRMED), (302, None, A.AUTHORITY_UNCONFIRMED), (429, None, A.AUTHORITY_UNCONFIRMED),
])
def test_response_classification_uses_status_and_api_code_not_message(monkeypatch, status, code, expected):
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        body = {"job_id": "job", "github_run_id": "owner", "state": "RUNNING", "execution_attempt_id": ATTEMPT,
                "lease_expires_at": (datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()}
        if status != 200: body = {"detail": {"code": code, "message": "private-body secret-token"}}
        return httpx.Response(status, json=body)
    monkeypatch.setattr(httpx, "request", request)
    client = RunnerControlClient("https://user:private-password@unused.invalid", "secret-token")
    result = client.check_authority(job_id="job", github_run_id="owner")
    assert result.status == expected
    assert len(calls) == 1 and calls[0]["follow_redirects"] is False
    assert all(s not in repr(result)+repr(client) for s in ("private-password", "private-body", "secret-token"))


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError])
def test_timeout_and_network_failure_are_unconfirmed_not_definitive_loss(monkeypatch, error):
    calls = []
    def request(*args, **kwargs):
        calls.append(1)
        raise error("https://user:password@private.invalid header=secret-token")
    monkeypatch.setattr(httpx, "request", request)
    result = RunnerControlClient("https://unused.invalid", "secret-token").check_authority(job_id="job", github_run_id="owner")
    assert result.status == A.AUTHORITY_UNCONFIRMED and len(calls) == 1
    assert "password" not in repr(result)


@pytest.mark.parametrize("change,expected", [
    ({"github_run_id": "other"}, A.LEASE_LOST), ({"state": "COMPLETED"}, A.LEASE_LOST),
    ({"job_id": "other"}, A.AUTHORITY_UNCONFIRMED), ({"lease_expires_at": "bad"}, A.AUTHORITY_UNCONFIRMED),
    ({"lease_expires_at": "2000-01-01T00:00:00+00:00"}, A.LEASE_LOST),
    ({"lease_expires_at": "2000-01-01T00:00:00"}, A.AUTHORITY_UNCONFIRMED),
])
def test_inconclusive_or_incompatible_acknowledgement(monkeypatch, change, expected):
    body = {"job_id": "job", "github_run_id": "owner", "state": "RUNNING", "execution_attempt_id": ATTEMPT,
            "lease_expires_at": (datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(), **change}
    monkeypatch.setattr(httpx, "request", lambda *a, **k: httpx.Response(200, json=body))
    assert RunnerControlClient("https://unused.invalid", "synthetic").check_authority(job_id="job", github_run_id="owner").status == expected


@pytest.mark.parametrize("status", [A.LEASE_LOST, A.AUTH_REJECTED, A.AUTHORITY_UNCONFIRMED, A.CONTROL_PLANE_ERROR])
def test_known_failure_blocks_next_work_and_reports_across_steps(tmp_path, status):
    control = Control([status])
    with supervisor(tmp_path, control) as s:
        assert s.run(python("raise Exception('must not execute')"), operation="work").status == "BLOCKED"
        assert s.run(python("raise Exception('must not execute')"), operation="other").status == "BLOCKED"
        assert s.report(state="FAILED", summary="late").status == "REPORT_BLOCKED"
    with supervisor(tmp_path, control) as s:
        assert s.run(python("pass"), operation="retry").status == "BLOCKED"
        assert s.report(state="COMPLETED", summary="late").status == "REPORT_BLOCKED"
    assert control.beats == 1 and not control.reports


def test_heartbeat_loss_kills_active_process_and_descendants(tmp_path):
    pidfile = tmp_path/"pids"
    source = f'''import os,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
open({str(pidfile)!r},'w').write(str(os.getpid())+' '+str(child.pid))
time.sleep(30)
'''
    class LossAfterStarted(Control):
        def check_authority(self, **kwargs):
            result = super().check_authority(**kwargs)
            # Inject loss only once both processes actually announced readiness.
            # A fixed second heartbeat races Python process startup on busy hosts.
            ready = pidfile.exists() and len(pidfile.read_text().split()) == 2
            return replace(result, status=A.LEASE_LOST) if ready else result
    control = LossAfterStarted()
    with supervisor(tmp_path, control, work_timeout_seconds=2) as s:
        result = s.run(python(source), operation="provider")
        assert result.status == "BLOCKED" and result.outcome_unknown
        assert s.run(python(source), operation="provider").status == "BLOCKED"
        assert s.run(python("pass"), operation="dispatch").status == "BLOCKED"
    for pid in map(int, pidfile.read_text().split()):
        with pytest.raises(ProcessLookupError): os.kill(pid, 0)
    assert not control.reports


def test_controlled_cancel_escalates_kill_and_reports_once_only_if_still_valid(tmp_path):
    pidfile = tmp_path/"pid"
    def heartbeat(number):
        if number == 2: s.cancel()
    control = Control(on_heartbeat=heartbeat)
    with supervisor(tmp_path, control) as s:
        result = s.run(python(f"import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(30)"), operation="work")
        assert result.status == "CANCELLED" and result.outcome_unknown
        assert s.report(state="COMPLETED", summary="incorrect success").status == "REPORT_BLOCKED"
        assert s.report(state="CANCELLED", summary="cancel").status == "REPORTED"
        assert s.report(state="CANCELLED", summary="retry").status == "REPORT_BLOCKED"
    assert [r["state"] for r in control.reports] == ["CANCELLED"]
    with pytest.raises(ProcessLookupError): os.kill(int(pidfile.read_text()), 0)


def test_normal_root_exit_also_cleans_a_background_child(tmp_path):
    pidfile = tmp_path/"child"
    with supervisor(tmp_path) as s:
        result = s.run(python(f"import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); open({str(pidfile)!r},'w').write(str(p.pid))"), operation="work")
    assert result.status == "COMPLETE"
    with pytest.raises(ProcessLookupError): os.kill(int(pidfile.read_text()), 0)


def test_startup_failure_partial_cleanup_and_no_operation_replay(tmp_path):
    control = Control()
    with supervisor(tmp_path, control) as s:
        assert s.run(["/nonexistent/amodex-r4"], operation="tool").status == "START_FAILED"
        assert s.run(python("pass"), operation="tool").status == "BLOCKED"
        assert s.report(state="FAILED", summary="startup failed").status == "REPORTED"
    # Lock and signal resources were released even on failure.
    with supervisor(tmp_path, control) as s:
        assert s.report(state="FAILED", summary="repeat").status == "REPORT_BLOCKED"


def test_initialization_failure_releases_local_lock(tmp_path):
    (tmp_path/"supervision.json").write_text("invalid json")
    with pytest.raises(ValueError):
        with supervisor(tmp_path): pass
    (tmp_path/"supervision.json").unlink()
    with supervisor(tmp_path) as s:
        assert s.run(python("pass"), operation="work").status == "COMPLETE"


def test_command_has_no_control_credential_and_normal_report_ends_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("AMODEX_RUNNER_CONTROL_TOKEN", "must-not-leak")
    monkeypatch.setenv("AMODEX_CONTROL_PLANE_URL", "https://user:password@private.invalid")
    control = Control()
    with supervisor(tmp_path, control) as s:
        assert s.run(python("import os; assert 'AMODEX_RUNNER_CONTROL_TOKEN' not in os.environ; assert 'AMODEX_CONTROL_PLANE_URL' not in os.environ"), operation="work").status == "COMPLETE"
        assert s.report(state="COMPLETED", summary="done").status == "REPORTED"
        beats = control.beats
        assert s.run(python("pass"), operation="after-report").status == "BLOCKED"
        assert control.beats == beats


def test_ambiguous_terminal_report_is_never_replayed(tmp_path):
    class LostReport(Control):
        def report(self, **kwargs):
            self.reports.append(kwargs)
            raise httpx.ReadTimeout("private body")
    control = LostReport()
    with supervisor(tmp_path, control) as s:
        assert s.run(python("pass"), operation="work").status == "COMPLETE"
        assert s.report(state="COMPLETED", summary="done").status == "REPORT_UNKNOWN"
    with supervisor(tmp_path, control) as s:
        assert s.report(state="FAILED", summary="retry fallback").status == "REPORT_BLOCKED"
        assert s.run(python("pass"), operation="new-operation").status == "BLOCKED"
    assert len(control.reports) == 1


def test_stale_started_operation_blocks_after_simulated_sigkill(tmp_path):
    # SIGKILL cannot run Python cleanup. Persisted STARTED is conservatively unknown.
    (tmp_path/"supervision.json").write_text(json.dumps({"job_id":JOB, "github_run_id":"owner", "operations":{"provider":"STARTED"}}))
    control = Control()
    with supervisor(tmp_path, control) as s:
        assert s.run(python("pass"), operation="provider").status == "BLOCKED"
        assert s.run(python("pass"), operation="dispatch").status == "BLOCKED"
        assert s.report(state="FAILED", summary="late").status == "REPORT_BLOCKED"
    assert control.beats == 0 and not control.reports


def test_mark_running_is_guarded_and_normal_work_can_follow(tmp_path):
    control = Control()
    with supervisor(tmp_path, control) as s:
        assert s.running().status == "COMPLETE"
        assert s.run(python("pass"), operation="validate").status == "COMPLETE"
        assert s.report(state="COMPLETED", summary="done").status == "REPORTED"
    assert len(control.reports) == 1


def test_unknown_running_ack_blocks_replay_and_following_work(tmp_path):
    class LostRunning(Control):
        calls = 0
        def running(self, **kwargs):
            self.calls += 1
            raise httpx.ReadTimeout("private raw data")
    control = LostRunning()
    with supervisor(tmp_path, control) as s:
        assert s.running().outcome_unknown
        assert s.running().status == "BLOCKED"
        assert s.run(python("pass"), operation="validate").status == "BLOCKED"
        assert s.report(state="FAILED", summary="late").status == "REPORT_BLOCKED"
    assert control.calls == 1 and not control.reports


def test_total_control_timeout_kills_work_and_stops_without_reconnect(tmp_path):
    class Stuck(Control):
        def check_authority(self, **kwargs):
            if self.beats:
                time.sleep(30)  # SIGALRM must bound this, without a leftover thread.
            return super().check_authority(**kwargs)
    control = Stuck()
    started = time.monotonic()
    with supervisor(tmp_path, control) as s:
        result = s.run(python("import time; time.sleep(30)"), operation="work")
        assert result.status == "BLOCKED" and result.authority == A.AUTHORITY_UNCONFIRMED
    assert time.monotonic()-started < 2
    assert control.beats == 1


def test_work_timeout_is_finite_and_does_not_retry(tmp_path):
    with supervisor(tmp_path, work_timeout_seconds=0.12) as s:
        assert s.run(python("import time; time.sleep(30)"), operation="work").status == "WORK_TIMEOUT"
        assert s.run(python("pass"), operation="work").status == "BLOCKED"


def test_expiry_between_approval_and_spawn_does_not_start_work(tmp_path, monkeypatch):
    marker = tmp_path/"forbidden"
    control = Control()
    with supervisor(tmp_path, control) as s:
        original = s._save
        def save():
            original()
            if s.state["operations"].get("work") == "STARTED":
                s._lease_deadline = time.monotonic()-1
        monkeypatch.setattr(s, "_save", save)
        result = s.run(python(f"open({str(marker)!r},'w').write('unsafe')"), operation="work")
        assert result.status == "BLOCKED" and result.authority == "LEASE_LOST"
    assert not marker.exists() and control.beats == 1


def test_cancel_during_cleanup_is_preserved_for_later_steps(tmp_path, monkeypatch):
    control = Control()
    with supervisor(tmp_path, control) as s:
        original = s._cleanup
        def cleanup(process):
            result = original(process)
            s.cancel()
            return result
        monkeypatch.setattr(s, "_cleanup", cleanup)
        assert s.run(python("pass"), operation="work").status == "CANCELLED"
    with supervisor(tmp_path, control) as s:
        assert s.run(python("pass"), operation="later").status == "BLOCKED"
        assert s.report(state="COMPLETED", summary="late").status == "REPORT_BLOCKED"
        assert s.report(state="CANCELLED", summary="cancel").status == "REPORTED"


def test_live_sigterm_reaches_supervisor_and_cleans_process_group(tmp_path):
    child = tmp_path/"child.pid"
    runner = tmp_path/"run.py"
    # Run in a separate main thread/process so real SIGTERM exercises the handler.
    runner.write_text(f'''from pathlib import Path
from amodex.runners.supervisor import RunnerSupervisor
from amodex.environments.product import RunnerReportRequest, RunnerTerminalReceipt

JOB = "019a3d70-0000-7000-8000-000000000001"
ATTEMPT = "019a3d70-0000-7000-8000-000000000002"
from test_runner_supervision_r4 import Control,python
with RunnerSupervisor(client=Control(),job_id='job',github_run_id='owner',state_file=Path({str(tmp_path/'state')!r}),interval_seconds=.03,grace_seconds=.08) as s:
 r=s.run(python("import os,time; open({str(child)!r},'w').write(str(os.getpid())); time.sleep(30)"),operation='work')
 print(r.status,flush=True)
''')
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(Path('src').resolve()), str(Path(__file__).parent)))}
    proc = subprocess.Popen([sys.executable, str(runner)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        end = time.monotonic()+4
        while not child.exists() and time.monotonic() < end:
            time.sleep(.01)
        assert child.exists()
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=4)
        assert proc.returncode == 0 and "CANCELLED" in stdout, stderr
        with pytest.raises(ProcessLookupError): os.kill(int(child.read_text()), 0)
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait()
