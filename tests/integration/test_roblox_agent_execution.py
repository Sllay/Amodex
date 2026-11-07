"""Deterministic model substitute through the production Hermes plugin surface."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from amodex.connectors.models import ConnectorResourceRecord, ConnectorResourceType
from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import Objective, RepositoryAuthority, Scope, TaskPackage
from amodex.integrations.roblox_cloud import EXECUTE_OPERATION, RobloxCloudTarget
from amodex.runtime.hermes_worker import EXPECTED_TOOL_NAMES, HermesScopedWorker, HermesWorkerError, register_hermes_plugin
from amodex.tools.roblox_luau import SECRET_NAME, TOOL_NAME, RobloxLuauTestTool, SupervisedLuauApproval
from amodex.tools.scoped_workspace import ScopedWorkspaceTools, SecretBroker

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/roblox/score_test.luau"
SOURCE = FIXTURE.read_text()
EXPECTED = [{"ok": True, "checks": 3, "score": 42}]
MARKER = "AMODEX_LUAU_OK checks=3 score=42"
TASK_PATH = "universes/123/places/456/versions/7/luau-execution-sessions/s/tasks/t"


@pytest.fixture
def harness(tmp_path):
    now = datetime.now(timezone.utc)
    task = TaskPackage(package_id=new_uuid7(), task_id=new_uuid7(), attempt_id=new_uuid7(),
        issued_at=now, expires_at=now+timedelta(minutes=5), objective=Objective(statement="Fix pure Luau scoring"),
        repository=RepositoryAuthority(repository_id="fixture", expected_base_sha="a"*40, task_branch="test/luau"),
        scope=Scope(read=("src/**",), write=("src/**",), protected=("src/protected.luau",),
                    allowed_operations=("read_file", "patch_file", "search_files", "run_tests", EXECUTE_OPERATION)))
    env = new_uuid7()
    resource = ConnectorResourceRecord(resource_id=new_uuid7(), connector_id=new_uuid7(),
        resource_type=ConnectorResourceType.ROBLOX_PLACE, external_id="456", name="fixture",
        metadata={"universe_id": "123"}, bound_environment_id=env, created_at=now, updated_at=now)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/score.luau").write_text(SOURCE)
    (tmp_path / "src/protected.luau").write_text("return 0")
    (tmp_path / "private.luau").write_text("return 0")
    broker = SecretBroker(values={SECRET_NAME: "synthetic-private-key"}, granted_names=(SECRET_NAME,))
    tools = ScopedWorkspaceTools(task=task, workspace=tmp_path, secrets=broker)
    approval = SupervisedLuauApproval(str(task.package_id), str(task.task_id), str(task.attempt_id),
        str(env), str(resource.resource_id), RobloxCloudTarget("123", "456", "7"),
        hashlib.sha256(SOURCE.encode()).hexdigest(), json.dumps(EXPECTED), (MARKER,), lambda: True)
    requests = []
    def respond(req):
        requests.append(req)
        if req.url.path.endswith("/logs"):
            return httpx.Response(200, json={"luauExecutionSessionTaskLogs": [{"messages": [MARKER]}]})
        return httpx.Response(200, json={"path": TASK_PATH, "state": "COMPLETE", "output": {"results": EXPECTED}})
    http = httpx.Client(transport=httpx.MockTransport(respond))
    tool = RobloxLuauTestTool(tools=tools, environment_id=env, resource=resource, approval=approval, http=http)
    # Unit tests replace only the external analyzer, never policy/registration.
    tool._analyze = lambda source: {"status": "PASS", "returncode": 0}
    worker = HermesScopedWorker(task=task, tools=tools, hermes_root=tmp_path,
        home_root=tmp_path / "home", base_url="http://unused.invalid", api_key="unused", roblox_tool=tool)
    yield SimpleNamespace(worker=worker, tool=tool, tools=tools, approval=approval, resource=resource,
                          requests=requests, http=http, respond=respond, workspace=tmp_path)
    worker._release_leases()
    tool.close()
    http.close()


def invoke(h, **params):
    return json.loads(h.worker.invoke_tool(TOOL_NAME, {"path": "src/score.luau", **params}))


def registry(worker):
    handlers, definitions = {}, []
    def register(**kwargs):
        handlers[kwargs["name"]] = kwargs["handler"]
        definitions.append({"type": "function", "function": kwargs["schema"]})
    with worker._bridge_activation():
        register_hermes_plugin(SimpleNamespace(register_hook=lambda *a: None, register_tool=register))
    return handlers, definitions


def test_tool_advertised_only_with_explicit_permission(harness):
    h = harness
    assert set(registry(h.worker)[0]) == EXPECTED_TOOL_NAMES | {TOOL_NAME}
    restricted = h.tools.task.model_copy(update={"scope": h.tools.task.scope.model_copy(
        update={"allowed_operations": ("read_file", "patch_file", "search_files", "run_tests")})})
    h.tools.task = restricted
    assert set(registry(h.worker)[0]) == EXPECTED_TOOL_NAMES
    assert invoke(h, remote=True)["ok"] is False
    assert not h.requests


def test_unconfigured_worker_keeps_original_surface(harness):
    harness.worker.roblox_tool = None
    assert set(registry(harness.worker)[0]) == EXPECTED_TOOL_NAMES
    assert not invoke(harness, remote=True)["ok"]
    assert not harness.requests


def test_previous_validator_tool_runs_and_still_denies_shell(harness):
    h = harness
    (h.workspace / "src/test_arithmetic.py").write_text("def test_add():\n    assert 2 + 3 == 5\n")
    result = json.loads(h.worker.invoke_tool("amodex_run_tests", {
        "argv": [sys.executable, "-m", "pytest", "src/test_arithmetic.py", "-q"], "timeout_seconds": 10}))
    assert result["ok"] and "1 passed" in result["stdout"]
    denied = json.loads(h.worker.invoke_tool("amodex_run_tests", {"argv": ["sh", "-c", "echo forbidden"]}))
    assert not denied["ok"] and denied["error_type"] == "CommandDenied"


@pytest.mark.parametrize("path", ["private.luau", "../outside.luau", "/tmp/score.luau", "src/score.py"])
def test_out_of_scope_file_refused(harness, path):
    assert not invoke(harness, path=path, remote=True)["ok"]
    assert not harness.requests


def test_protected_file_and_alias_cannot_be_written(harness):
    h = harness
    (h.workspace / "src/alias.luau").symlink_to("protected.luau")
    for path in ("src/protected.luau", "src/alias.luau"):
        result = json.loads(h.worker.invoke_tool("amodex_patch_file", {"path": path,
            "expected_sha256": hashlib.sha256(b"return 0").hexdigest(), "old_text": "0", "new_text": "42"}))
        assert not result["ok"] and result["error_type"] == "ScopeViolation"


@pytest.mark.parametrize("change", ["universe", "place", "environment", "resource", "attempt", "expired", "revoked"])
def test_target_and_authority_mismatch_refused_before_post(harness, change):
    h = harness
    if change == "universe": h.tool.resource.metadata["universe_id"] = "999"
    elif change == "place": h.tool.resource.external_id = "999"
    elif change == "environment": h.tool.environment_id = new_uuid7()
    elif change == "resource": h.tool.resource.resource_id = new_uuid7()
    elif change == "attempt": h.tool.approval = replace(h.approval, attempt_id=str(new_uuid7()))
    elif change == "expired": h.tools.task = h.tools.task.model_copy(update={"expires_at": datetime.now(timezone.utc)-timedelta(seconds=1)})
    else: h.tool.approval = replace(h.approval, authority_check=lambda: False)
    assert not invoke(h, remote=True)["ok"]
    assert not h.requests


def test_secret_missing_does_not_fall_back_to_environment(harness, monkeypatch):
    monkeypatch.setenv(SECRET_NAME, "forbidden-global-key")
    harness.tools.secrets = SecretBroker()
    assert invoke(harness, remote=True)["code"] == "SECRET_UNAVAILABLE"
    assert not harness.requests


@pytest.mark.parametrize("status", ["FAIL", "ERROR", "UNAVAILABLE"])
def test_local_validation_failure_blocks_submission(harness, status):
    harness.tool._analyze = lambda source: {"status": status}
    assert invoke(harness, remote=True)["status"] == "LOCAL_BLOCKED"
    assert not harness.requests


def test_hash_review_and_explicit_remote_flag(harness):
    h = harness
    assert invoke(h)["status"] == "LOCAL_PASS"
    assert not h.requests
    (h.workspace / "src/score.luau").write_text(SOURCE + "\n-- unreviewed change")
    assert not invoke(h, remote=True)["ok"]
    assert not h.requests
    assert not invoke(h, remote="true")["ok"]
    assert not invoke(h, remote=True, universe_id="999")["ok"]


@pytest.mark.parametrize("failure,code,status", [
    (401, "UNAUTHENTICATED", "BLOCKED"), (403, "PERMISSION_DENIED", "BLOCKED"),
    (500, "HTTP_ERROR", "UNKNOWN_OUTCOME"), ("timeout", "HTTP_TIMEOUT", "UNKNOWN_OUTCOME"),
    ("bad_receipt", "INVALID_TASK_RECEIPT", "UNKNOWN_OUTCOME"),
])
def test_http_and_ambiguous_results_never_resubmit(harness, failure, code, status):
    h = harness
    def fail(req):
        h.requests.append(req)
        if failure == "timeout": raise httpx.ReadTimeout("private-detail", request=req)
        if failure == "bad_receipt": return httpx.Response(200, json={"path": "invalid"})
        return httpx.Response(failure, text="private-detail")
    h.tool._http = httpx.Client(transport=httpx.MockTransport(fail))
    result = invoke(h, remote=True)
    assert result["status"] == status and result["code"] == code and not result["resubmit"]
    assert "private-detail" not in json.dumps(result)
    assert invoke(h, remote=True)["code"] == "SUBMISSION_ALREADY_USED"
    assert [r.method for r in h.requests] == ["POST"]
    h.tool._http.close()


@pytest.mark.parametrize("state,results,expected", [
    ("COMPLETE", EXPECTED, "PASS"), ("COMPLETE", [{"ok": False, "checks": 3, "score": 0}], "TEST_FAILED"),
    ("COMPLETE", [{"ok": 1, "checks": 3, "score": 42}], "TEST_FAILED"), ("FAILED", [], "TEST_FAILED"),
])
def test_complete_is_not_automatically_a_pass(harness, state, results, expected):
    h = harness
    def respond(req):
        if req.url.path.endswith("/logs"): return h.respond(req)
        h.requests.append(req)
        return httpx.Response(200, json={"path": TASK_PATH, "state": state, "output": {"results": results}})
    h.tool._http = httpx.Client(transport=httpx.MockTransport(respond))
    result = invoke(h, remote=True)
    assert result["status"] == expected and result["state"] == state
    assert result["verified_results"] == (EXPECTED if expected == "PASS" else None)
    h.tool._http.close()


def test_logs_are_limited_sanitized_and_required(harness):
    h = harness
    def respond(req):
        if req.url.path.endswith("/logs"):
            h.requests.append(req)
            return httpx.Response(200, json={"luauExecutionSessionTaskLogs": [{"messages": [
                "synthetic-private-key", "Authorization: Bearer abc123", "token=hidden", "https://private.invalid/?key=hidden",
                "\x1b" + "x"*500, *["extra"]*50, MARKER]}]})
        return h.respond(req)
    h.tool._http = httpx.Client(transport=httpx.MockTransport(respond))
    result = invoke(h, remote=True)
    assert result["status"] == "TEST_FAILED"  # evidence beyond output limit is not accepted
    assert result["logs_truncated"] and len(result["logs"]) == 40
    assert all(len(line) <= 200 for line in result["logs"])
    assert all(s not in json.dumps(result) for s in ("synthetic-private-key", "abc123", "hidden", "private.invalid"))
    h.tool._http.close()


def test_interrupt_and_known_authority_loss_prevent_further_requests(harness):
    h = harness
    def respond(req):
        h.requests.append(req)
        h.worker.interrupt()
        return httpx.Response(200, json={"path": TASK_PATH, "state": "PROCESSING"})
    h.tool._http = httpx.Client(transport=httpx.MockTransport(respond))
    result = invoke(h, remote=True)
    assert result["status"] == "UNKNOWN_OUTCOME"
    assert result["task_path"] == TASK_PATH
    assert [r.method for r in h.requests] == ["POST"]
    assert not invoke(h, remote=True)["ok"]
    h.tool._http.close()


def test_concurrent_and_repeated_success_cannot_submit_twice(harness):
    h = harness
    nested = []
    def respond(req):
        if req.method == "POST": nested.append(invoke(h, remote=True))
        return h.respond(req)
    h.tool._http = httpx.Client(transport=httpx.MockTransport(respond))
    assert invoke(h, remote=True)["ok"]
    assert nested[0]["code"] == "TOOL_BUSY"
    assert invoke(h, remote=True)["code"] == "SUBMISSION_ALREADY_USED"
    assert [r.method for r in h.requests].count("POST") == 1
    h.tool._http.close()


def test_real_local_analyzer_passes_and_rejects_invalid_luau(harness):
    analyzer = os.environ.get("AMODEX_TEST_LUAU_ANALYZER")
    if not analyzer: pytest.skip("set AMODEX_TEST_LUAU_ANALYZER to trusted official luau-analyze")
    tool = harness.tool
    tool.analyzer = Path(analyzer)
    tool._analyze = RobloxLuauTestTool._analyze.__get__(tool)
    assert invoke(harness)["status"] == "LOCAL_PASS"
    (harness.workspace / "src/score.luau").write_text("local = broken !!!")
    assert invoke(harness)["status"] == "LOCAL_FAIL"
    assert not invoke(harness, remote=True)["ok"]
    assert not harness.requests


def test_deterministic_agent_edits_analyzes_executes_and_reads_result_through_worker_run(harness, monkeypatch):
    h = harness
    (h.workspace / "src/score.luau").write_text(SOURCE.replace("return math.clamp(coins, 0, 100) * multiplier", "return 0"))
    events, handlers, definitions = [], {}, []
    def register(**kwargs):
        handlers[kwargs["name"]] = kwargs["handler"]
        definitions.append({"type": "function", "function": kwargs["schema"]})
    def discover(**kwargs):
        register_hermes_plugin(SimpleNamespace(register_hook=lambda *a: None, register_tool=register))
    def call(name, **kwargs):
        result = json.loads(handlers[name](kwargs))
        events.append((name, result))
        assert result["ok"], result
        return result
    class DeterministicAgent:
        def __init__(self, **kwargs):
            assert kwargs["max_iterations"] == 12
        def run_conversation(self, **kwargs):
            read = call("amodex_read_file", path="src/score.luau")
            call("amodex_search_files", pattern="return 0")
            call("amodex_patch_file", path="src/score.luau", expected_sha256=read["sha256"],
                 old_text="return 0", new_text="return math.clamp(coins, 0, 100) * multiplier")
            call(TOOL_NAME, path="src/score.luau")
            result = call(TOOL_NAME, path="src/score.luau", remote=True)
            assert result["verified_results"] == EXPECTED and result["logs"] == [MARKER]
            return {"final_response": "Modified src/score.luau; COMPLETE; checks=3; score=42"}
        def close(self): pass
    monkeypatch.setattr(h.worker, "_verify_hermes", lambda: None)
    monkeypatch.setitem(sys.modules, "model_tools", SimpleNamespace(get_tool_definitions=lambda **kw: definitions))
    monkeypatch.setitem(sys.modules, "hermes_cli", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", SimpleNamespace(discover_plugins=discover))
    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=DeterministicAgent))
    response = h.worker.run(instruction="Fix score and run the approved pure test once",
                            capsule=h.tools.build_context_capsule(("src/score.luau",)))
    assert "checks=3; score=42" in response
    assert (h.workspace / "src/score.luau").read_text() == SOURCE
    assert [r.method for r in h.requests] == ["POST", "GET"]
    assert [name for name, _ in events] == ["amodex_read_file", "amodex_search_files", "amodex_patch_file", TOOL_NAME, TOOL_NAME]
    assert not h.worker._leases


def test_surface_verification_still_rejects_unexpected_tools(harness, monkeypatch):
    h = harness
    monkeypatch.setattr(h.worker, "_verify_hermes", lambda: None)
    monkeypatch.setitem(sys.modules, "model_tools", SimpleNamespace(get_tool_definitions=lambda **kw:
        [{"function": {"name": n}} for n in h.worker.expected_tool_names | {"shell"}]))
    monkeypatch.setitem(sys.modules, "hermes_cli", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", SimpleNamespace(discover_plugins=lambda **kw: None))
    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=None))
    with pytest.raises(HermesWorkerError, match="surface drift"):
        h.worker.run(instruction="fixture", capsule=h.tools.build_context_capsule(("src/score.luau",)))
    assert not h.requests
