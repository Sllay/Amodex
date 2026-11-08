from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from amodex.environments.product import RunnerJobHeartbeatRequest, RunnerJobIdentityRequest, RunnerReportRequest, RunnerTerminalReceipt
from amodex.runners.client import RunnerControlClient, build_parser


def test_runner_job_heartbeat_requires_github_run_id() -> None:
    request = RunnerJobHeartbeatRequest(github_run_id="987654321")
    assert request.github_run_id == "987654321"

    with pytest.raises(ValidationError):
        RunnerJobHeartbeatRequest(github_run_id="")


def test_runner_client_posts_heartbeat_with_workflow_identity(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"job_id": "job-123", "state": "RUNNING"},
            text="",
        )

    monkeypatch.setattr("amodex.runners.client.httpx.request", fake_request)
    client = RunnerControlClient(
        base_url="https://control.example",
        token="runner-secret",
    )

    result = client.heartbeat(job_id="job-123", github_run_id="987654321")

    assert result["state"] == "RUNNING"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://control.example/v1/runner-jobs/job-123/heartbeat"
    assert captured["headers"] == {"authorization": "Bearer runner-secret"}
    assert captured["json"] == {"github_run_id": "987654321"}


@pytest.mark.parametrize("model,extra", [(RunnerJobIdentityRequest, {}),
    (RunnerJobHeartbeatRequest, {}), (RunnerReportRequest, {"state": "COMPLETED", "summary": "synthetic", "attempt_id": "019a3d70-0000-7000-8000-000000000002"})])
def test_all_mutation_contracts_require_identity(model, extra):
    for identity in [None, "", " ", "a b", "x"*101]:
        with pytest.raises(ValidationError):
            model(github_run_id=identity, **extra)
    with pytest.raises(ValidationError):
        model(**extra)


@pytest.mark.parametrize("operation", ["running", "report"])
def test_client_sends_required_owner_in_json(monkeypatch, operation):
    captured = {}
    job = "019a3d70-0000-7000-8000-000000000001"
    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        if operation == "report":
            body = RunnerReportRequest(**kwargs["json"])
            value = RunnerTerminalReceipt(receipt_id=job,job_id=job,attempt_id=body.attempt_id,
                github_run_id=body.github_run_id,report_digest=body.digest(job),state=body.state,
                summary=body.summary,result_commit=None,evidence={},audit_id=job,
                environment_id=job,session_id=job,run_id=job,committed_at=datetime.now(timezone.utc)).model_dump(mode="json")
            return SimpleNamespace(status_code=200,json=lambda:value)
        return SimpleNamespace(status_code=200, json=lambda: {"state": "RUNNING"})
    monkeypatch.setattr("amodex.runners.client.httpx.request", fake_request)
    client = RunnerControlClient(base_url="https://local.test", token="synthetic")
    extra = {"state": "FAILED", "summary": "synthetic", "attempt_id": "019a3d70-0000-7000-8000-000000000002"} if operation == "report" else {}
    getattr(client, operation)(job_id=job, github_run_id="987654321", **extra)
    assert captured["json"]["github_run_id"] == "987654321"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/"+operation)


@pytest.mark.parametrize("operation", ["claim", "running", "heartbeat", "report"])
def test_cli_requires_workflow_identity(operation):
    argv = [operation, "synthetic-job"]
    if operation == "report":
        argv += ["--state", "FAILED", "--summary", "synthetic", "--attempt-id", "019a3d70-0000-7000-8000-000000000002"]
    with pytest.raises(SystemExit) as result:
        build_parser().parse_args(argv)
    assert result.value.code == 2
    assert build_parser().parse_args([*argv, "--github-run-id", "987654321"]).github_run_id == "987654321"
