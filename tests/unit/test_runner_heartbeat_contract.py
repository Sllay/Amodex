from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from amodex.environments.product import RunnerJobHeartbeatRequest
from amodex.runners.client import RunnerControlClient


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
