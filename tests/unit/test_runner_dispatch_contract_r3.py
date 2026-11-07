"""No network calls: header propagation and external ambiguity contract."""
import httpx
import pytest

from amodex.cli import AmodexClient, build_parser, execute
from amodex.environments.product import require_idempotency_key
from amodex.runners.dispatch import GitHubActionsDispatcher, RunnerDispatchError


def test_cli_requires_explicit_stable_key_and_reuses_it(monkeypatch):
    parser = build_parser()
    argv = ["env", "dispatch", "environment", "run", "session"]
    with pytest.raises(SystemExit): parser.parse_args(argv)
    args = parser.parse_args([*argv, "--idempotency-key", "owner-intent-123"])
    captured = []
    def request(method, url, **kwargs):
        captured.append(kwargs)
        return httpx.Response(202, json={"job": {"job_id": "same-job"}})
    monkeypatch.setattr(httpx, "request", request)
    client = AmodexClient("https://unused.invalid")
    execute(args, client)
    execute(args, client)
    assert len(captured) == 2
    assert captured[0]["headers"] == captured[1]["headers"] == {"Idempotency-Key": "owner-intent-123"}
    assert captured[0]["json"] == captured[1]["json"]


@pytest.mark.parametrize("key", ["", "contains spaces", "slash/key", "á", "x"*129, "\ninvalid"])
def test_key_validation(key):
    with pytest.raises(ValueError): require_idempotency_key(key)


@pytest.mark.parametrize("status", [204, 401, 403, 500, 302, "timeout"])
def test_external_dispatch_sends_once_does_not_redirect_and_sanitizes_error(monkeypatch, status):
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        if status == "timeout": raise httpx.ReadTimeout("synthetic-private-value")
        return httpx.Response(status, text="synthetic-private-value")
    monkeypatch.setattr(httpx, "post", post)
    dispatcher = GitHubActionsDispatcher(repository="fixture/repo", token="synthetic-private-value")
    if status == 204:
        dispatcher.dispatch(job_id="job")
    else:
        with pytest.raises(RunnerDispatchError) as error: dispatcher.dispatch(job_id="job")
        assert "synthetic-private-value" not in str(error.value)
    assert len(calls) == 1 and calls[0]["follow_redirects"] is False
    assert calls[0]["json"]["inputs"] == {"job_id": "job"}
    assert dispatcher.correlation() == {"repository": "fixture/repo", "workflow": "environment-runner.yml", "ref": "main"}
