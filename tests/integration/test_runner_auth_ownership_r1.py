"""Real workspace composition, private gate and local SQL; no permissive auth mocks."""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlparse
import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.api.workspace_app import create_workspace_app
from amodex.deployment.migrations import apply_production_migrations, database_endpoint_identity
from amodex.runners import client as runner_cli
from test_postbaseline_environment_product import (
    RecordingDispatcher, _create_scoped_run, environment_product_state,
)

TOKEN = "synthetic-r1-control-credential"
AUTH = {"authorization": f"Bearer {TOKEN}"}
OWNER = "424242"


@pytest.fixture(scope="session", autouse=True)
def local_database_only():
    target = urlparse(os.environ["AMODEX_TEST_DATABASE_URL"])
    assert target.hostname in {"localhost", "127.0.0.1", "::1"}
    assert target.path.startswith("/amodex_qa_")


@pytest.fixture
def workspace(request, environment_product_state, monkeypatch, tmp_path):
    runs, product, environments, jobs = environment_product_state
    # Web readiness validates the complete checksum manifest, not only Runner SQL.
    # Keep this regression runnable against a new, isolated local test database.
    apply_production_migrations(runtime_dsn=runs.dsn, migration_dsn=runs.dsn,
        endpoint_guard=database_endpoint_identity(runs.dsn), migrations_root="migrations")
    token = getattr(request, "param", TOKEN)
    monkeypatch.setenv("AMODEX_ACCESS_PASSCODE", "246810")
    monkeypatch.setenv("AMODEX_ACCESS_SESSION_SECRET", "synthetic-r1-human-session-signing-key-0001")
    # The gate must use the base control plane authority, not a second env read.
    monkeypatch.setenv("AMODEX_RUNNER_CONTROL_TOKEN", "different-synthetic-environment-value")
    if token is None:
        monkeypatch.delenv("AMODEX_RUNNER_CONTROL_TOKEN")
    monkeypatch.setenv("AMODEX_MEDIA_STORE_BACKEND", "file")
    monkeypatch.setenv("AMODEX_MEDIA_STORE_DIR", str(tmp_path/"media"))
    monkeypatch.setenv("AMODEX_CONNECTOR_VAULT_DIR", str(tmp_path/"vault"))
    base = create_app(runs, product_repository=product, environment_repository=environments,
                      environment_product_repository=jobs, runner_dispatcher=RecordingDispatcher(),
                      runner_control_token=token)
    app = create_workspace_app(base_app=base, dsn=runs.dsn)
    with TestClient(app, base_url="https://workspace.test", follow_redirects=False) as human:
        assert human.post("/auth/passcode", data={"passcode": "246810"}).status_code == 303
        _, env, session, run = _create_scoped_run(human, name="R1 fixture", engine="generic",
                                                 repo_url="https://github.com/qa-local-fixture/r1")
        created = human.post(f"/v1/environments/{env['environment_id']}/runner-jobs", headers={"Idempotency-Key": "fixture-intent"}, json={
            "run_id": run["run_id"], "session_id": session["session_id"],
            "worker_profile": "implementation", "task_class": "ROUTINE", "write_paths": ["src/**"],
        })
        assert created.status_code == 201
        job_id = created.json()["job"]["job_id"]
        with TestClient(app, base_url="https://workspace.test", follow_redirects=False) as machine:
            yield SimpleNamespace(app=app, human=human, machine=machine, jobs=jobs, product=product,
                                  job_id=job_id, prefix=f"/v1/runner-jobs/{job_id}", env=env)


def mutate(case, action, *, client=None, headers=None, owner=OWNER):
    body = {"github_run_id": owner}
    if action == "report":
        body.update(state="COMPLETED", summary="synthetic validation")
    return (client or case.machine).post(case.prefix+"/"+action, headers=headers,
                                        params=body if action == "claim" else None,
                                        json=None if action == "claim" else body)


def test_machine_completes_all_four_routes_without_cookie(workspace):
    case = workspace
    for action, state in [("claim", "CLAIMED"), ("running", "RUNNING"),
                          ("heartbeat", "RUNNING"), ("report", "COMPLETED")]:
        response = mutate(case, action, headers=AUTH)
        assert response.status_code == 200, (action, response.text)
        assert case.jobs.get_runner_job(case.job_id).state.value == state
        assert not case.machine.cookies


@pytest.mark.parametrize("action", ["claim", "running", "heartbeat", "report"])
@pytest.mark.parametrize("credentials", [None, "incorrect", "configured"])
def test_authentication_precedes_missing_identity_or_malformed_json(workspace, action, credentials):
    case = workspace
    headers = {} if credentials is None else {"authorization": f"Bearer {TOKEN if credentials == 'configured' else 'incorrect'}"}
    before = case.jobs.get_runner_job(case.job_id)
    audits = len(case.product.list_audit())
    response = case.machine.post(case.prefix+"/"+action, json={}, headers=headers)
    assert response.status_code == (422 if credentials == "configured" else 401)
    malformed = case.machine.post(case.prefix+"/"+action, content="{broken",
                                  headers={**headers, "content-type": "application/json"})
    assert malformed.status_code == (422 if credentials == "configured" else 401)
    assert case.jobs.get_runner_job(case.job_id) == before
    assert len(case.product.list_audit()) == audits


@pytest.mark.parametrize("workspace", [None, ""], indirect=True)
def test_unconfigured_token_fails_closed_even_with_human_session(workspace):
    for client in [workspace.machine, workspace.human]:
        for action in ["claim", "running", "heartbeat", "report"]:
            response = mutate(workspace, action, client=client, headers=AUTH)
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "RUNNER_AUTH_NOT_CONFIGURED"
    assert workspace.jobs.get_runner_job(workspace.job_id).state.value == "DISPATCHED"


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE"])
def test_bearer_does_not_bypass_private_gate_for_other_methods(workspace, method):
    for action in ["claim", "running", "heartbeat", "report"]:
        assert workspace.machine.request(method, workspace.prefix+"/"+action, headers=AUTH).status_code == 401
    assert workspace.jobs.get_runner_job(workspace.job_id).state.value == "DISPATCHED"


@pytest.mark.parametrize("suffix", ["", "/claim/", "/claiming", "/claim/extra", "/claims", "/report/extra"])
def test_similar_paths_are_not_machine_routes(workspace, suffix):
    assert workspace.machine.post(workspace.prefix+suffix, headers=AUTH).status_code == 401


def test_machine_bearer_does_not_grant_human_api_access(workspace):
    paths = ["/", "/legacy", "/models", "/connections", "/docs", "/v1/projects",
             "/v1/workspaces", "/v1/environments", "/v1/system/status", "/v1/model-intelligence/routes",
             "/v1/audit", "/v1/checkpoints", "/v1/evidence", "/v1/provider-connections", "/v1/runner-jobs",
             workspace.prefix, f"/v1/environments/{workspace.env['environment_id']}/memories"]
    for path in paths:
        assert workspace.machine.get(path, headers=AUTH).status_code == 401, path
    for path in ["/v1/projects", "/v1/provider-connections", "/v1/workspaces",
                 f"/v1/environments/{workspace.env['environment_id']}/memories"]:
        assert workspace.machine.post(path, headers=AUTH, json={}).status_code == 401
    assert workspace.machine.post("/v1/runner-jobs/not-a-uuid/claim", headers=AUTH).status_code == 401
    # A second environment read must not override the base application's token.
    assert mutate(workspace, "claim", headers={"authorization": "Bearer different-synthetic-environment-value"}).status_code == 401


def test_human_session_alone_never_authorizes_runner_mutations(workspace):
    for action in ["claim", "running", "heartbeat", "report"]:
        assert mutate(workspace, action, client=workspace.human).status_code == 401
        assert mutate(workspace, action, client=workspace.human,
                      headers={"authorization": "Bearer incorrect"}).status_code == 401
    assert workspace.human.get("/v1/projects").status_code == 200
    assert workspace.human.get("/v1/workspaces").status_code == 200
    assert workspace.human.get("/v1/provider-connections").status_code == 200
    assert workspace.jobs.get_runner_job(workspace.job_id).state.value == "DISPATCHED"


@pytest.mark.parametrize("identity", [None, "", " ", "workflow a", "x"*101, 42])
def test_identity_shape_is_enforced_before_any_job_mutation(workspace, identity):
    case = workspace
    for action in ["claim", "running", "heartbeat", "report"]:
        # Query identities are strings by transport; integer shape is a body-only case.
        if action == "claim" and identity == 42:
            continue
        before = case.jobs.get_runner_job(case.job_id)
        assert mutate(case, action, headers=AUTH, owner=identity).status_code == 422
        assert case.jobs.get_runner_job(case.job_id) == before


def test_wrong_owner_cannot_run_renew_or_report(workspace):
    case = workspace
    assert mutate(case, "claim", headers=AUTH).status_code == 200
    for action in ["running", "heartbeat", "report"]:
        before = case.jobs.get_runner_job(case.job_id)
        assert mutate(case, action, headers=AUTH, owner="different-workflow").status_code == 409
        assert case.jobs.get_runner_job(case.job_id) == before


@pytest.mark.parametrize("phase", ["DISPATCHED", "DISPATCH_UNKNOWN"])
@pytest.mark.parametrize("terminal", ["COMPLETED", "FAILED", "CANCELLED"])
def test_terminal_report_requires_claim(workspace, phase, terminal):
    case = workspace
    if phase != "DISPATCHED":
        with psycopg.connect(case.jobs.dsn) as conn:
            conn.execute("UPDATE amodex_environment_runner_jobs SET state=%s WHERE job_id=%s", (phase, case.job_id))
    response = case.machine.post(case.prefix+"/report", headers=AUTH,
                                 json={"github_run_id": OWNER, "state": terminal, "summary": "pre-claim"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUNNER_JOB_TERMINAL_CONFLICT"
    assert case.jobs.get_runner_job(case.job_id).state.value == phase


@pytest.mark.parametrize("terminal", ["FAILED", "CANCELLED"])
def test_claimed_owner_can_report_failure_or_cancellation(workspace, terminal):
    assert mutate(workspace, "claim", headers=AUTH).status_code == 200
    response = workspace.machine.post(workspace.prefix+"/report", headers=AUTH,
                                     json={"github_run_id": OWNER, "state": terminal, "summary": "post-claim"})
    assert response.status_code == 200


def test_expired_owner_cannot_renew_run_or_report(workspace):
    case = workspace
    assert mutate(case, "claim", headers=AUTH).status_code == 200
    with psycopg.connect(case.jobs.dsn) as conn:
        conn.execute("UPDATE amodex_environment_runner_jobs SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE job_id=%s", (case.job_id,))
    before = case.jobs.get_runner_job(case.job_id)
    for action in ["running", "heartbeat", "report"]:
        assert mutate(case, action, headers=AUTH).status_code == 409
        assert case.jobs.get_runner_job(case.job_id) == before


def test_public_health_and_human_login_logout_contracts(workspace):
    case = workspace
    assert case.machine.get("/").status_code == 401
    assert case.machine.get("/auth/passcode").status_code == 200
    assert case.machine.get("/healthz").json() == {"status": "ok"}
    ready = case.machine.get("/readyz")
    assert ready.status_code == 200 and ready.json()["ready"] is True
    assert case.human.get("/").status_code == 200
    logout = case.human.post("/auth/logout")
    assert logout.status_code == 303
    assert logout.headers["clear-site-data"] == '"cache", "storage"'
    assert case.human.get("/v1/projects").status_code == 401


def test_actual_cli_and_client_work_through_the_real_workspace_gate(workspace, monkeypatch, tmp_path, capsys):
    case = workspace
    monkeypatch.setenv("AMODEX_CONTROL_PLANE_URL", "https://workspace.test")
    monkeypatch.setenv("AMODEX_RUNNER_CONTROL_TOKEN", TOKEN)
    def local_transport(method, url, **kwargs):
        return case.machine.request(method, url, **{k:kwargs[k] for k in ["headers", "params", "json"] if k in kwargs})
    # Only the HTTP transport is local; all real middleware/handlers/SQL execute.
    monkeypatch.setattr(runner_cli.httpx, "request", local_transport)
    claim_file = tmp_path/"claim.json"
    commands = [["claim", case.job_id, "--output", str(claim_file)],
                ["running", case.job_id], ["heartbeat", case.job_id],
                ["report", case.job_id, "--state", "COMPLETED", "--summary", "synthetic CLI report"]]
    for command in commands:
        runner_cli.main([*command, "--github-run-id", OWNER])
    assert claim_file.is_file()
    assert case.jobs.get_runner_job(case.job_id).state.value == "COMPLETED"
    assert not case.machine.cookies
    assert TOKEN not in capsys.readouterr().out
