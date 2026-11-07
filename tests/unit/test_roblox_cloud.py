"""Open Cloud contract using local fixtures and MockTransport; never remote execution."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from amodex.connectors.models import ConnectorResourceRecord, ConnectorResourceType
from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import Objective, RepositoryAuthority, Scope, TaskPackage
from amodex.integrations.models import IntegrationCapability
from amodex.integrations.roblox_cloud import (
    BINDING, EXECUTE_OPERATION, LuauTask, RobloxCloudError, RobloxCloudTarget, RobloxLuauClient,
)
from amodex.tools.scoped_workspace import ScopedWorkspaceTools, ScopeViolation, SecretBroker

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/roblox"
FIXTURE = json.loads((FIXTURES / "open_cloud.json").read_text())
PATH = FIXTURE["path"]


class Clock:
    now = 0.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


def client(handler, *, clock=None, follow_redirects=False):
    clock = clock or Clock()
    return RobloxLuauClient(target=RobloxCloudTarget("123", "456", "7"), api_key="synthetic-private-key",
        http=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=follow_redirects),
        clock=clock, sleep=clock.advance)


def test_fixture_submit_poll_logs_and_assertions_are_separate_from_http_success():
    requests = []
    states, pages = iter(FIXTURE["states"]), iter(FIXTURE["log_pages"])
    def handler(req):
        requests.append(req)
        assert req.url.host == "apis.roblox.com"
        assert req.headers["x-api-key"] == "synthetic-private-key"
        assert not req.headers.get("cookie")
        if req.url.path.endswith("/logs"):
            assert req.url.params["view"] == "FLAT"
            assert req.url.params["maxPageSize"] == "100"
            return httpx.Response(200, json=next(pages))
        if req.method == "POST":
            assert req.url.path == "/cloud/v2/universes/123/places/456/versions/7/luau-execution-session-tasks"
            assert json.loads(req.content) == {"script": (FIXTURES / "score_test.luau").read_text(), "timeout": "10s"}
        else:
            assert req.url.path == "/cloud/v2/" + PATH
            assert req.url.params["view"] == "BASIC"
        state = next(states)
        return httpx.Response(200, json={"path": PATH, "state": state, **({"output": FIXTURE["output"]} if state == "COMPLETE" else {})})
    api = client(handler)
    result = api.wait(api.submit((FIXTURES / "score_test.luau").read_text()))
    assert result.state == "COMPLETE"
    assert result.results == ({"ok": True, "checks": 3, "score": 42},)
    assert api.collect_logs(result.path) == ("AMODEX_LUAU_OK checks=3 score=42",)
    assert requests[-1].url.params["pageToken"] == "fixture-page-2"
    assert [r.method for r in requests] == ["POST", "GET", "GET", "GET", "GET"]
    assert not {IntegrationCapability.INPUT, IntegrationCapability.SCREENSHOT} & set(BINDING.capabilities)


@pytest.mark.parametrize("status,code,ambiguous", [
    (400, "HTTP_ERROR", False), (401, "UNAUTHENTICATED", False),
    (403, "PERMISSION_DENIED", False), (429, "RATE_LIMITED", False),
    (500, "HTTP_ERROR", True), (503, "HTTP_ERROR", True), (302, "HTTP_ERROR", True),
])
def test_submission_http_failures_never_retry_or_follow_redirect(status, code, ambiguous):
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(status, json={"message": "do-not-echo-private-body"},
                              headers={"Location": "https://untrusted.invalid/credential-sink", "Retry-After": "1"})
    with pytest.raises(RobloxCloudError) as error:
        client(handler, follow_redirects=True).submit("return 42")
    assert error.value.code == code and error.value.http_status == status
    assert error.value.ambiguous_outcome is ambiguous
    assert len(requests) == 1
    assert "private" not in str(error.value)


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadError])
@pytest.mark.parametrize("operation", ["submit", "poll"])
def test_transport_failure_is_ambiguous_without_retry(error_type, operation):
    requests = []
    def handler(req):
        requests.append(req)
        raise error_type("do-not-echo-private-key", request=req)
    api = client(handler)
    with pytest.raises(RobloxCloudError) as error:
        api.submit("return 42") if operation == "submit" else api.wait(LuauTask(PATH, "PROCESSING"))
    assert error.value.ambiguous_outcome is True
    assert len(requests) == 1
    assert error.value.task_path == (PATH if operation == "poll" else None)
    assert "private-key" not in str(error.value)
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize("body", [
    {"path": PATH, "state": "COMPLETE"},
    {"path": PATH, "state": "COMPLETE", "output": {"results": "invalid"}},
    {"path": PATH, "state": "COMPLETE", "output": {"results": []}, "error": {}},
    {"path": PATH, "state": "NEW_UNKNOWN_STATE"},
    {"path": PATH, "state": {"invalid": "type"}},
    {"path": "https://untrusted.invalid/tasks/task", "state": "QUEUED"},
    {"path": PATH.replace("places/456", "places/999"), "state": "QUEUED"},
    {"path": PATH.replace("versions/7", "versions/8"), "state": "QUEUED"},
    {"path": "universes/123/places/456/luau-execution-session-tasks/legacy", "state": "QUEUED"},
    [], None,
])
def test_bad_success_receipt_is_ambiguous_not_permission_to_resubmit(body):
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(200, text="not json")
    with pytest.raises(RobloxCloudError) as error:
        client(handler).submit("return 42")
    assert error.value.ambiguous_outcome is True
    assert len(requests) == 1


@pytest.mark.parametrize("path", ["../secrets", "https://untrusted.invalid/task", PATH+"?secret=1", PATH+"/../other", PATH.replace("universes/123", "universes/999")])
def test_untrusted_poll_paths_never_send_credentials(path):
    def handler(req):
        pytest.fail("untrusted URL requested")
    with pytest.raises(RobloxCloudError, match="UNTRUSTED_TASK_PATH"):
        client(handler).get_task(path)


@pytest.mark.parametrize("terminal,code", [("FAILED", "SCRIPT_ERROR"), ("FAILED", "DEADLINE_EXCEEDED"), ("CANCELLED", None)])
def test_script_failure_and_remote_timeout_are_terminal_without_replay(terminal, code):
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, json={"path": PATH, "state": terminal, "error": {"code": code}})
    result = client(handler).wait(LuauTask(PATH, "QUEUED"))
    assert result.state == terminal and result.results is None and result.error_code == code
    assert len(requests) == 1


def test_poll_deadline_and_max_polls_preserve_receipt_and_do_not_cancel():
    requests, clock = [], Clock()
    def handler(req):
        requests.append(req)
        assert req.extensions["timeout"]["read"] <= 1.5
        return httpx.Response(200, json={"path": PATH, "state": "PROCESSING"})
    api = client(handler, clock=clock)
    with pytest.raises(RobloxCloudError) as error:
        api.wait(LuauTask(PATH, "QUEUED"), deadline_seconds=2.5, poll_interval_seconds=1)
    assert error.value.code == "POLL_DEADLINE" and error.value.task_path == PATH
    assert error.value.ambiguous_outcome is True
    assert clock.now == 2.5 and len(requests) == 2
    assert all(r.method == "GET" for r in requests)
    requests.clear()
    with pytest.raises(RobloxCloudError, match="POLL_DEADLINE"):
        api.wait(LuauTask(PATH, "PROCESSING"), deadline_seconds=2.5, poll_interval_seconds=1, max_polls=1)
    assert len(requests) == 1


def test_poll_http_failure_does_not_restart_submission():
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(429, json={})
    with pytest.raises(RobloxCloudError) as error:
        client(handler).wait(LuauTask(PATH, "PROCESSING"))
    assert error.value.task_path == PATH and error.value.ambiguous_outcome
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("response,code", [
    ({"luauExecutionSessionTaskLogs": [{"messages": [42]}]}, "INVALID_LOGS"),
    ({"luauExecutionSessionTaskLogs": "invalid"}, "INVALID_LOGS"),
    ({"luauExecutionSessionTaskLogs": [], "nextPageToken": "repeated"}, "LOGS_INCOMPLETE"),
])
def test_logs_validate_data_and_bound_pagination(response, code):
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, json=response)
    with pytest.raises(RobloxCloudError, match=code):
        client(handler).collect_logs(PATH, max_pages=2)
    assert len(requests) <= 2


@pytest.mark.parametrize("kwargs", [{"deadline_seconds": 0}, {"deadline_seconds": float("nan")},
    {"poll_interval_seconds": 0}, {"max_polls": True}, {"max_polls": 61}])
def test_invalid_poll_limits_never_request(kwargs):
    with pytest.raises(ValueError):
        client(lambda req: pytest.fail("invalid poll request")).wait(LuauTask(PATH, "QUEUED"), **kwargs)


@pytest.mark.parametrize("script,timeout", [("", 10), ("x"*64001, 10), ("return 42", 0), ("return 42", 301), ("return 42", True)],
                         ids=["empty-script", "oversize-script", "minimum-timeout", "maximum-timeout", "boolean-timeout"])
def test_invalid_script_limits_never_request(script, timeout):
    with pytest.raises(ValueError):
        client(lambda req: pytest.fail("invalid submission")).submit(script, script_timeout_seconds=timeout)


def test_scoped_file_and_selected_connector_resource_are_reused(tmp_path):
    now = datetime.now(timezone.utc)
    env, connector, resource_id = new_uuid7(), new_uuid7(), new_uuid7()
    resource = ConnectorResourceRecord(resource_id=resource_id, connector_id=connector,
        resource_type=ConnectorResourceType.ROBLOX_PLACE, external_id="456", name="synthetic",
        metadata={"universe_id": "123"}, bound_environment_id=env, created_at=now, updated_at=now)
    assert RobloxCloudTarget.from_resource(resource, environment_id=env) == RobloxCloudTarget("123", "456")
    with pytest.raises(ScopeViolation):
        RobloxCloudTarget.from_resource(resource, environment_id=new_uuid7())
    task = TaskPackage(package_id=new_uuid7(), task_id=new_uuid7(), attempt_id=new_uuid7(),
        issued_at=now, expires_at=now+timedelta(minutes=10), objective=Objective(statement="Synthetic Luau test"),
        repository=RepositoryAuthority(repository_id="fixture", expected_base_sha="a"*40, task_branch="test/roblox"),
        scope=Scope(read=("score_test.luau",), allowed_operations=("read_file", EXECUTE_OPERATION)))
    (tmp_path / "score_test.luau").write_text((FIXTURES / "score_test.luau").read_text())
    (tmp_path / "private.luau").write_text("return 0")
    broker = SecretBroker(values={"ROBLOX_OPEN_CLOUD_API_KEY": "synthetic-private-key"}, granted_names=("ROBLOX_OPEN_CLOUD_API_KEY",))
    tools = ScopedWorkspaceTools(task=task, workspace=tmp_path, secrets=broker)
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, json={"path": PATH, "state": "QUEUED"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        api = RobloxLuauClient(target=RobloxCloudTarget.from_resource(resource, environment_id=env, version_id="7"),
                              api_key=tools.read_secret("ROBLOX_OPEN_CLOUD_API_KEY"), http=http)
        assert api.submit_scoped(tools, "score_test.luau").state == "QUEUED"
        with pytest.raises(ScopeViolation):
            api.submit_scoped(tools, "private.luau")
        restricted = tools.task.model_copy(update={"scope": Scope(read=("score_test.luau",), allowed_operations=("read_file",))})
        with pytest.raises(ScopeViolation):
            api.submit_scoped(ScopedWorkspaceTools(task=restricted, workspace=tmp_path), "score_test.luau")
    assert len(requests) == 1


def test_smoke_cli_defaults_to_explicit_simulation_and_blocks_unconfigured_live(monkeypatch):
    for name in ["AMODEX_ROBLOX_TEST_UNIVERSE_ID", "AMODEX_ROBLOX_TEST_PLACE_ID", "AMODEX_ROBLOX_OPEN_CLOUD_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    simulated = subprocess.run([sys.executable, "scripts/roblox_luau_smoke.py"], text=True, capture_output=True, check=True)
    assert json.loads(simulated.stdout)["status"] == "SIMULATED_PASS"
    live = subprocess.run([sys.executable, "scripts/roblox_luau_smoke.py", "--live"], text=True, capture_output=True)
    assert live.returncode == 2 and json.loads(live.stdout)["status"] == "LIVE_BLOCKED"
