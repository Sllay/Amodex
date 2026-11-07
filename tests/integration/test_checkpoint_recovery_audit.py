"""Characterization, not approval: some assertions demonstrate current defects.

Only loopback PostgreSQL, local Git fixtures and deterministic fakes are used.
No production behavior is changed by this audit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.api.access_gate import AccessGateConfig, install_access_gate
from amodex.environments.product import RunnerJobState
from amodex.git.task_workspace import GitTaskWorkspace, GitWorkspaceError
from amodex.product.models import CheckpointView
from amodex.providers.governor import ProviderGovernor
from amodex.providers.simulator import DeterministicProvider
from amodex.runners.dispatch import RunnerDispatchError
from amodex.runtime.models import EventKind, ProviderRequestState, RunState
from amodex.runtime.worker import Stage1Worker
from amodex.storage.environment import EnvironmentStateError
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.postgres import PostgresRunRepository, StaleLeaseError
from amodex.tools.scoped_workspace import ScopedWorkspaceTools, ToolPolicyError

# Reuse the existing fixtures and setup contracts rather than a second framework.
from test_postbaseline_environment_product import (
    RecordingDispatcher, _create_scoped_run, environment_product_state,
)
from test_stage2_checkpoint_lifecycle import _create_fixture_remote, _git, _task
from test_stage3_dag_orchestration import _plan
from amodex.director import DagOrchestrator

TOKEN = "synthetic-local-audit-token"
AUTH = {"authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="session", autouse=True)
def local_audit_database():
    target = urlparse(os.environ["AMODEX_TEST_DATABASE_URL"])
    assert target.hostname in {"localhost", "127.0.0.1", "::1"}
    assert target.path.startswith("/amodex_qa_"), "audit requires an explicitly disposable database"


@pytest.fixture
def audit(environment_product_state):
    runs, product, environments, jobs = environment_product_state
    dispatcher = RecordingDispatcher()
    app = create_app(runs, product_repository=product,
                     environment_repository=environments, environment_product_repository=jobs,
                     runner_dispatcher=dispatcher, runner_control_token=TOKEN)
    with TestClient(app, raise_server_exceptions=False) as client:
        _, env, session, run = _create_scoped_run(
            client, name="recovery audit", engine="generic",
            repo_url="https://github.com/qa-local-fixture/disposable")
        body = {"run_id": run["run_id"], "session_id": session["session_id"],
                "worker_profile": "implementation", "task_class": "ROUTINE", "write_paths": ["src/**"]}
        url = f"/v1/environments/{env['environment_id']}/runner-jobs"
        response = client.post(url, json=body)
        assert response.status_code == 201
        job_id = response.json()["job"]["job_id"]
        yield client, runs, product, jobs, dispatcher, job_id, run["run_id"], url, body


def expire(jobs, job_id):
    with psycopg.connect(jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE job_id=%s", (job_id,))


def claim(client, job_id, owner="workflow-a"):
    response = client.post(f"/v1/runner-jobs/{job_id}/claim", params={"github_run_id": owner}, headers=AUTH)
    assert response.status_code == 200
    return response


def test_running_currently_has_no_owner_parameter(audit):
    client, _, _, jobs, _, job_id, *_ = audit
    claim(client, job_id)
    assert client.post(f"/v1/runner-jobs/{job_id}/running").status_code == 401
    # A holder of the shared runner token need not prove it is workflow-a.
    response = client.post(f"/v1/runner-jobs/{job_id}/running", headers=AUTH)
    assert response.status_code == 200
    assert jobs.get_runner_job(job_id).github_run_id == "workflow-a"


def test_private_access_gate_currently_blocks_machine_claim_even_with_valid_runner_token(audit):
    _, runs, product, jobs, _, job_id, *_ = audit
    app = create_app(runs, product_repository=product, environment_product_repository=jobs,
                     runner_dispatcher=RecordingDispatcher(), runner_control_token=TOKEN)
    install_access_gate(app, AccessGateConfig(passcode="246810", session_secret="synthetic-local-session-signing-key-0001",
                                              session_ttl_seconds=3600))
    with TestClient(app, base_url="https://local-fixture.test") as machine:
        path = f"/v1/runner-jobs/{job_id}/claim?github_run_id=workflow-a"
        blocked = machine.post(path, headers=AUTH)
        assert blocked.status_code == 401
        assert blocked.json()["detail"] == "Amodex access passcode required"
        assert jobs.get_runner_job(job_id).state == RunnerJobState.DISPATCHED
        # Adding the synthetic owner session proves the request reaches the
        # separate runner bearer check. Actual workflows never acquire it.
        assert machine.post("/auth/passcode", data={"passcode": "246810"}).status_code == 200
        assert machine.post(path, headers=AUTH).status_code == 200


def test_omitted_terminal_identity_currently_bypasses_owner_check_and_does_not_complete_run(audit):
    client, runs, _, jobs, _, job_id, run_id, *_ = audit
    claim(client, job_id)
    path = f"/v1/runner-jobs/{job_id}/report"
    body = {"state": "COMPLETED", "summary": "synthetic report"}
    wrong = client.post(path, json={**body, "github_run_id": "other-workflow"}, headers=AUTH)
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["code"] == "RUNNER_LEASE_OWNER_MISMATCH"
    accepted = client.post(path, json=body, headers=AUTH)
    assert accepted.status_code == 200
    assert jobs.get_runner_job(job_id).state == RunnerJobState.COMPLETED
    assert accepted.json()["result_commit"] is None  # No validation evidence is required.
    assert runs.get_run(run_id).state == RunState.QUEUED
    assert len(runs.list_events(run_id)) == 1


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELLED"])
def test_unclaimed_dispatched_job_currently_accepts_identityless_terminal_report(audit, state):
    client, _, _, jobs, _, job_id, *_ = audit
    response = client.post(f"/v1/runner-jobs/{job_id}/report", headers=AUTH,
                           json={"state": state, "summary": "synthetic pre-claim report"})
    assert response.status_code == 200
    assert jobs.get_runner_job(job_id).claimed_at is None


def test_expired_runner_lease_rejects_renewal_running_and_report_without_reclaim(audit):
    client, _, _, jobs, _, job_id, *_ = audit
    claim(client, job_id)
    expire(jobs, job_id)
    assert client.post(f"/v1/runner-jobs/{job_id}/heartbeat", headers=AUTH,
                       json={"github_run_id": "workflow-a"}).status_code == 409
    assert client.post(f"/v1/runner-jobs/{job_id}/running", headers=AUTH).status_code == 409
    for owner in ["workflow-a", None]:
        response = client.post(f"/v1/runner-jobs/{job_id}/report", headers=AUTH,
                               json={"state": "COMPLETED", "summary": "late", "github_run_id": owner})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "RUNNER_LEASE_LOST"
    assert client.post(f"/v1/runner-jobs/{job_id}/claim", headers=AUTH,
                       params={"github_run_id": "replacement"}).status_code == 409
    assert jobs.get_runner_job(job_id).state == RunnerJobState.CLAIMED


def test_two_database_claimants_have_exactly_one_winner(audit):
    _, _, _, jobs, _, job_id, *_ = audit
    barrier = Barrier(2)
    def competing_claim(owner):
        barrier.wait(timeout=5)
        try:
            payload = PostgresEnvironmentProductRepository(jobs.dsn).claim_runner_job(job_id=job_id, github_run_id=owner)
            return payload["job"].github_run_id
        except EnvironmentStateError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(competing_claim, ["workflow-a", "workflow-b"]))
    assert outcomes.count("RUNNER_JOB_NOT_CLAIMABLE") == 1
    assert jobs.get_runner_job(job_id).github_run_id in outcomes
    assert jobs.get_runner_job(job_id).heartbeat_count == 1


@pytest.mark.parametrize("ambiguous", [False, True])
def test_claim_before_dispatch_ack_currently_returns_500_but_preserves_claim(audit, ambiguous):
    client, _, _, jobs, dispatcher, _, _, url, body = audit
    def early_claim(*, job_id):
        dispatcher.job_ids.append(job_id)
        jobs.claim_runner_job(job_id=job_id, github_run_id="fast-workflow")
        if ambiguous:
            raise RunnerDispatchError("synthetic ambiguous acknowledgement")
    dispatcher.dispatch = early_claim
    response = client.post(url, json=body)
    assert response.status_code == 500
    raced_id = dispatcher.job_ids[-1]
    assert jobs.get_runner_job(raced_id).state == RunnerJobState.CLAIMED
    assert jobs.get_runner_job(raced_id).github_run_id == "fast-workflow"


def test_repeated_dispatch_post_currently_creates_another_job_and_external_attempt(audit):
    client, _, _, _, dispatcher, job_id, _, url, body = audit
    repeated = client.post(url, json=body)
    assert repeated.status_code == 201
    assert repeated.json()["job"]["job_id"] != job_id
    assert len(dispatcher.job_ids) == 2


def test_report_retry_is_conflict_after_completion_not_idempotent_success(audit):
    client, _, product, _, _, job_id, *_ = audit
    claim(client, job_id)
    body = {"state": "COMPLETED", "summary": "same result", "github_run_id": "workflow-a"}
    path = f"/v1/runner-jobs/{job_id}/report"
    assert client.post(path, json=body, headers=AUTH).status_code == 200
    repeated = client.post(path, json=body, headers=AUTH)
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "RUNNER_JOB_TERMINAL_CONFLICT"
    assert sum(e.action == "RUNNER_JOB_COMPLETED" for e in product.list_audit()) == 1


def test_terminal_commit_and_audit_are_currently_separate_transactions(audit, monkeypatch):
    client, _, product, jobs, _, job_id, *_ = audit
    claim(client, job_id)
    original = product.append_audit
    def fail_audit(**kwargs):
        if kwargs["action"] == "RUNNER_JOB_COMPLETED":
            raise RuntimeError("synthetic audit persistence failure")
        return original(**kwargs)
    monkeypatch.setattr(product, "append_audit", fail_audit)
    body = {"state": "COMPLETED", "summary": "result persisted before audit", "github_run_id": "workflow-a"}
    path = f"/v1/runner-jobs/{job_id}/report"
    assert client.post(path, json=body, headers=AUTH).status_code == 500
    assert jobs.get_runner_job(job_id).state == RunnerJobState.COMPLETED
    assert not any(e.action == "RUNNER_JOB_COMPLETED" for e in product.list_audit())
    assert client.post(path, json=body, headers=AUTH).status_code == 409


def test_run_currently_accepts_expired_owner_until_another_claim_fences_it(audit):
    _, runs, _, _, _, _, run_id, *_ = audit
    old = runs.claim_next(worker_id="old-worker")
    assert old.run_id == run_id
    with psycopg.connect(runs.dsn) as conn:
        conn.execute("UPDATE amodex_runs SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
    accepted = runs.transition(run_id=run_id, worker_id="old-worker", fencing_token=old.fencing_token,
                               state=RunState.RUNNING, kind=EventKind.PROVIDER_ADMITTED)
    assert accepted.state == RunState.RUNNING
    replacement = runs.claim_next(worker_id="new-worker")
    assert replacement.fencing_token > old.fencing_token
    with pytest.raises(StaleLeaseError):
        runs.transition(run_id=run_id, worker_id="old-worker", fencing_token=old.fencing_token,
                        state=RunState.COMPLETED, kind=EventKind.RUN_COMPLETED)


def test_external_response_lost_before_persistence_is_not_recalled_on_recovery(audit, monkeypatch):
    _, runs, _, _, _, _, run_id, *_ = audit
    class CountingProvider(DeterministicProvider):
        calls = 0
        def generate(self, **kwargs):
            self.calls += 1
            return super().generate(**kwargs)
    provider = CountingProvider()
    def lose_completion(**kwargs):
        raise RuntimeError("synthetic crash after external response, before DB completion")
    monkeypatch.setattr(runs, "mark_provider_request_completed", lose_completion)
    worker = Stage1Worker(runs, provider, ProviderGovernor.stage1_default(), "old-worker")
    with pytest.raises(RuntimeError, match="synthetic crash"):
        worker.process_once()
    assert provider.calls == 1
    assert runs.get_run(run_id).provider_request_state == ProviderRequestState.STARTED
    with psycopg.connect(runs.dsn) as conn:
        conn.execute("UPDATE amodex_runs SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
    fresh = PostgresRunRepository(runs.dsn)
    result = Stage1Worker(fresh, provider, ProviderGovernor.stage1_default(), "replacement").process_once()
    assert result.state == RunState.UNKNOWN_OUTCOME
    assert provider.calls == 1
    events = fresh.list_events(run_id)
    assert [e.seq for e in events] == list(range(1, len(events)+1))


def test_run_event_failure_rolls_back_state_and_revision(audit, monkeypatch):
    _, runs, _, _, _, _, run_id, *_ = audit
    owned = runs.claim_next(worker_id="worker")
    before = runs.list_events(run_id)
    def fail_event(*args, **kwargs):
        raise RuntimeError("synthetic event write failure")
    monkeypatch.setattr(runs, "_insert_event", fail_event)
    with pytest.raises(RuntimeError, match="synthetic event"):
        runs.transition(run_id=run_id, worker_id="worker", fencing_token=owned.fencing_token,
                        state=RunState.COMPLETED, kind=EventKind.RUN_COMPLETED)
    assert runs.get_run(run_id).revision == owned.revision
    assert runs.get_run(run_id).state == RunState.CLAIMED
    assert runs.list_events(run_id) == before


def test_stage1_currently_commits_response_after_lease_expired_during_call(audit):
    _, runs, _, _, _, _, run_id, *_ = audit
    class ExpiringProvider(DeterministicProvider):
        def generate(self, **kwargs):
            # Deterministically simulate a call outliving the lease, no sleeping.
            with psycopg.connect(runs.dsn) as conn:
                conn.execute("UPDATE amodex_runs SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
            return super().generate(**kwargs)
    result = Stage1Worker(runs, ExpiringProvider(), ProviderGovernor.stage1_default(), "expired-worker").process_once()
    assert result.state == RunState.COMPLETED
    assert result.provider_request_state == ProviderRequestState.COMPLETED


def test_checkpoint_view_is_mutable_projection_not_immutable_git_evidence(audit):
    _, _, product, _, _, _, run_id, *_ = audit
    view = CheckpointView(checkpoint_id="synthetic-projection", run_id=run_id, task_id="task",
                          checkpoint_class="SAFE", commit_sha="a"*40, created_at=datetime.now(timezone.utc))
    product.upsert_checkpoint_view(view)
    product.upsert_checkpoint_view(view.model_copy(update={"checkpoint_class": "VALIDATED", "commit_sha": "b"*40}))
    fresh = type(product)(product.dsn)
    persisted = next(item for item in fresh.list_checkpoint_views() if item.checkpoint_id == view.checkpoint_id)
    assert persisted.commit_sha == "b"*40 and persisted.evidence_ids == ()


def test_partial_safe_publication_leaves_remote_commit_but_no_checkpoint_ref(tmp_path, monkeypatch):
    remote, base = _create_fixture_remote(tmp_path)
    task = _task(base)
    ws = GitTaskWorkspace.create(task=task, repository_url=str(remote), path=tmp_path/"worker")
    path = ws.path/"src/calculator.py"
    path.write_text(path.read_text().replace("raise NotImplementedError", "return max(low, min(value, high))"))
    original = GitTaskWorkspace._git
    def fail_ref_push(self, *args):
        if args[0] == "push" and args[-1].endswith(ws.safe_ref()):
            raise GitWorkspaceError("synthetic checkpoint ref publication failure")
        return original(self, *args)
    monkeypatch.setattr(GitTaskWorkspace, "_git", fail_ref_push)
    with pytest.raises(GitWorkspaceError, match="synthetic checkpoint"):
        ws.commit_safe_checkpoint(message="synthetic safe checkpoint")
    sha = ws.current_sha()
    assert sha != base
    assert _git(tmp_path, "--git-dir", str(remote), "rev-parse", f"refs/heads/{task.repository.task_branch}") == sha
    missing = subprocess.run(["git", "--git-dir", str(remote), "show-ref", "--verify", "--quiet", ws.safe_ref()])
    assert missing.returncode == 1
    with pytest.raises(GitWorkspaceError, match="actual authorized change"):
        ws.commit_safe_checkpoint(message="retry without a receipt")


def test_patch_repetition_is_precondition_failure_not_operation_receipt(tmp_path):
    remote, base = _create_fixture_remote(tmp_path)
    task = _task(base)
    ws = GitTaskWorkspace.create(task=task, repository_url=str(remote), path=tmp_path/"worker")
    tools = ScopedWorkspaceTools(task=task, workspace=ws.path)
    original = tools.read_file("src/calculator.py")
    import hashlib
    lease = tools.acquire_write_lease("src/calculator.py", owner="worker")
    args = dict(owner="worker", lease_id=lease.lease_id, expected_sha256=hashlib.sha256(original.encode()).hexdigest(),
                old_text="raise NotImplementedError", new_text="return max(low, min(value, high))")
    sha = tools.patch_file("src/calculator.py", **args)
    with pytest.raises(ToolPolicyError, match="stale patch precondition"):
        tools.patch_file("src/calculator.py", **args)
    assert hashlib.sha256((ws.path/"src/calculator.py").read_bytes()).hexdigest() == sha
    # A fresh registry has no knowledge of the still-held previous lease.
    fresh = ScopedWorkspaceTools(task=task, workspace=ws.path)
    assert fresh.acquire_write_lease("src/calculator.py", owner="other-worker")


def test_dag_json_can_restore_in_fresh_process_but_running_authority_is_not_reconciled(tmp_path):
    plan = _plan()
    dag = DagOrchestrator(plan)
    lease = dag.start("A")
    state = tmp_path/"dag.json"
    state.write_text(json.dumps({"plan": plan.to_dict(), "snapshot": dag.snapshot()}))
    code = """
import json,sys
from amodex.director import DagOrchestrator,TaskPlan
p=json.load(open(sys.argv[1]))
d=DagOrchestrator.restore(TaskPlan.from_dict(p['plan']),p['snapshot'])
print(json.dumps({'state':d.states['A'].value,'ownership':d.ownership.snapshot()}))
"""
    result = subprocess.run([sys.executable, "-c", code, str(state)], check=True, capture_output=True, text=True, timeout=10)
    restored = json.loads(result.stdout)
    assert restored["state"] == "RUNNING"
    assert restored["ownership"]["leases"][0]["fencing_token"] == lease.fencing_token
