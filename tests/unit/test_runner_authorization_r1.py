from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from amodex.api.runner_auth import RunnerAuthorization, is_runner_machine_route
from amodex.environments.product import RunnerJobState
from amodex.storage.environment_product import PostgresEnvironmentProductRepository


def request(headers=(), method="POST", path="/v1/runner-jobs/00000000-0000-7000-8000-000000000001/claim"):
    return Request({"type": "http", "method": method, "scheme": "https", "path": path,
                    "headers": list(headers), "query_string": b"", "server": ("local.test", 443)})


@pytest.mark.parametrize("headers", [[], [(b"authorization", b"Bearer incorrect")],
    [(b"authorization", b"Bearer synthetic"), (b"authorization", b"Bearer synthetic")],
    [(b"authorization", b"Bearer \xff")]])
def test_invalid_headers_are_fail_closed_without_echoing_credentials(headers):
    auth = RunnerAuthorization("synthetic")
    with pytest.raises(HTTPException) as result:
        auth.require(request(headers))
    assert result.value.status_code == 401
    assert result.value.detail == "invalid runner authorization"
    assert "synthetic" not in repr(auth)


@pytest.mark.parametrize("token", [None, "", " "])
def test_unconfigured_machine_authority_does_not_authenticate(token):
    with pytest.raises(HTTPException) as result:
        RunnerAuthorization(token).require(request([(b"authorization", b"Bearer synthetic")]))
    assert result.value.status_code == 503


@pytest.mark.parametrize("action", ["claim", "running", "heartbeat", "report"])
def test_only_exact_post_machine_routes_are_recognized(action):
    path = "/v1/runner-jobs/00000000-0000-7000-8000-000000000001/" + action
    assert is_runner_machine_route(request(path=path))
    assert not is_runner_machine_route(request(path=path, method="GET"))
    assert not is_runner_machine_route(request(path=path+"/"))
    assert not is_runner_machine_route(request(path=path+"-extra"))
    assert not is_runner_machine_route(request(path="/prefix"+path))


@pytest.mark.parametrize("identity", [None, "", " ", "a b", "a\n", "x"*101])
def test_repository_never_opens_a_connection_for_invalid_identity(monkeypatch, identity):
    def unexpected_connection(*args, **kwargs):
        raise AssertionError("identity must be checked before any SQL")
    monkeypatch.setattr("amodex.storage.environment_product.psycopg.connect", unexpected_connection)
    repo = PostgresEnvironmentProductRepository("postgresql://local.invalid/synthetic")
    for method in [repo.claim_runner_job, repo.mark_running, repo.heartbeat_runner_job, repo.report_runner_job]:
        kwargs = {"job_id": "synthetic", "github_run_id": identity}
        if method.__name__ == "report_runner_job":
            kwargs.update(state=RunnerJobState.COMPLETED, summary="synthetic", result_commit=None)
        with pytest.raises(ValueError, match="github_run_id"):
            method(**kwargs)


def test_workflow_sends_identity_and_only_reports_failure_after_claim():
    workflow = Path(".github/workflows/environment-runner.yml").read_text()
    def step(name):
        return workflow.split(f"      - name: {name}\n", 1)[1].split("\n      - name:", 1)[0]
    for name in ["Mark runner job active", "Report successful validation", "Report runner failure"]:
        assert '--github-run-id "$GITHUB_RUN_ID"' in step(name)
    claim = step("Claim exact durable runner job")
    assert claim.index('RUNNER_JOB_CLAIMED=true') > claim.index('python -m amodex.runners.client claim')
    assert "failure() && env.RUNNER_JOB_CLAIMED == 'true'" in step("Report runner failure")
