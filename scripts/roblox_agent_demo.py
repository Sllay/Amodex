#!/usr/bin/env python3
"""Scripted agent through Hermes' registered tools; no model inference.

Default HTTP is simulated. --live explicitly spends one supervised submission
on the pure, reviewed fixture, with no POST retries. Never rerun after ambiguity.
The resource binding here is an ephemeral demonstration, not a Web DB binding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from amodex.connectors.models import ConnectorResourceRecord, ConnectorResourceType
from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import Objective, RepositoryAuthority, Scope, TaskPackage
from amodex.integrations.roblox_cloud import EXECUTE_OPERATION, RobloxCloudTarget
from amodex.runtime.hermes_worker import HermesScopedWorker, register_hermes_plugin
from amodex.tools.roblox_luau import SECRET_NAME, TOOL_NAME, RobloxLuauTestTool, SupervisedLuauApproval
from amodex.tools.scoped_workspace import ScopedWorkspaceTools, SecretBroker

ROOT = Path(__file__).resolve().parents[1]
BASE = "758854741a5d13f61c6e7f6b4ae8ac31461cadd1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--universe-id", required=True)
    parser.add_argument("--place-id", required=True)
    parser.add_argument("--analyzer", type=Path, required=True)
    args = parser.parse_args()
    if args.live and not os.environ.get(SECRET_NAME):
        print(json.dumps({"status": "LIVE_BLOCKED", "code": "SECRET_UNAVAILABLE"}))
        return 2
    target = RobloxCloudTarget(args.universe_id, args.place_id)
    source = (ROOT / "tests/fixtures/roblox/score_test.luau").read_text()
    broken = source.replace("return math.clamp(coins, 0, 100) * multiplier", "return 0")
    expected = [{"ok": True, "checks": 3, "score": 42}]
    marker = "AMODEX_LUAU_OK checks=3 score=42"
    now = datetime.now(timezone.utc)
    task = TaskPackage(package_id=new_uuid7(), task_id=new_uuid7(), attempt_id=new_uuid7(),
        issued_at=now, expires_at=now+timedelta(minutes=5),
        objective=Objective(statement="Fix pure Luau score; analyze and test once under supervision"),
        repository=RepositoryAuthority(repository_id="Sllay/Amodex", expected_base_sha=BASE,
                                       task_branch="feat/roblox-agent-execution-v1"),
        scope=Scope(read=("score.luau",), write=("score.luau",),
                    allowed_operations=("read_file", "patch_file", EXECUTE_OPERATION)))
    environment_id = new_uuid7()
    resource = ConnectorResourceRecord(resource_id=new_uuid7(), connector_id=new_uuid7(),
        resource_type=ConnectorResourceType.ROBLOX_PLACE, external_id=target.place_id,
        name="Supervised disposable fixture target", metadata={"universe_id": target.universe_id},
        bound_environment_id=environment_id, created_at=now, updated_at=now)
    approval = SupervisedLuauApproval(str(task.package_id), str(task.task_id), str(task.attempt_id),
        str(environment_id), str(resource.resource_id), target, hashlib.sha256(source.encode()).hexdigest(),
        json.dumps(expected), (marker,), lambda: True)
    broker = SecretBroker(values={SECRET_NAME: os.environ[SECRET_NAME] if args.live else "synthetic-demo-key"},
                          granted_names=(SECRET_NAME,))
    receipt = f"universes/{target.universe_id}/places/{target.place_id}/versions/7/luau-execution-sessions/demo/tasks/demo"
    def simulate(request):
        if request.method == "POST":
            assert json.loads(request.content)["script"] == source
            return httpx.Response(200, json={"path": receipt, "state": "PROCESSING"})
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"luauExecutionSessionTaskLogs": [{"messages": [marker]}]})
        return httpx.Response(200, json={"path": receipt, "state": "COMPLETE", "output": {"results": expected}})
    statuses = []
    def observe(response):
        statuses.append({"method": response.request.method, "http_status": response.status_code})
    with tempfile.TemporaryDirectory(prefix="amodex-agent-demo-") as directory:
        workspace = Path(directory)
        (workspace / "score.luau").write_text(broken)
        tools = ScopedWorkspaceTools(task=task, workspace=workspace, secrets=broker)
        with httpx.Client(transport=None if args.live else httpx.MockTransport(simulate),
                          timeout=10, follow_redirects=False, event_hooks={"response": [observe]}) as http:
            tool = RobloxLuauTestTool(tools=tools, environment_id=environment_id, resource=resource,
                                      approval=approval, analyzer=args.analyzer, http=http)
            worker = HermesScopedWorker(task=task, tools=tools, hermes_root=ROOT,
                home_root=workspace / "home", base_url="http://unused.invalid", api_key="unused", roblox_tool=tool)
            handlers, actions = {}, []
            def register(**kwargs):
                handlers[kwargs["name"]] = kwargs["handler"]
            def call(name, **kwargs):
                result = json.loads(handlers[name](kwargs))
                actions.append({"tool": name, "ok": result["ok"]})
                return result
            try:
                with worker._bridge_activation():
                    register_hermes_plugin(SimpleNamespace(register_hook=lambda *a: None, register_tool=register))
                    read = call("amodex_read_file", path="score.luau")
                    patch = call("amodex_patch_file", path="score.luau", expected_sha256=read["sha256"],
                                 old_text="return 0", new_text="return math.clamp(coins, 0, 100) * multiplier")
                    local = call(TOOL_NAME, path="score.luau") if patch["ok"] else patch
                    result = call(TOOL_NAME, path="score.luau", remote=True) if local["ok"] else local
                status = ("LIVE_PASS" if args.live else "SIMULATED_PASS") if result["ok"] else result.get("status", "BLOCKED")
                print(json.dumps({"status": status, "agent": "deterministic_scripted",
                    "real_model_inference": False, "remote_executed": args.live and any(s["method"] == "POST" for s in statuses),
                    "modified_files": ["score.luau (temporary workspace)"],
                    "before_sha256": read.get("sha256"), "after_sha256": patch.get("sha256"),
                    "actions": actions, "http": statuses, "result": result}, sort_keys=True))
                return 0 if result["ok"] else 2
            finally:
                worker._release_leases()
                tool.close()


if __name__ == "__main__":
    raise SystemExit(main())
