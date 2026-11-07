#!/usr/bin/env python3
"""Explicit simulation or one authorized pure-logic cloud test; never publishes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

from amodex.integrations.roblox_cloud import RobloxCloudError, RobloxCloudTarget, RobloxLuauClient
from amodex.tools.scoped_workspace import SecretBroker

FIXTURES = Path(__file__).resolve().parents[1] / "tests/fixtures/roblox"
EXPECTED = {"ok": True, "checks": 3, "score": 42}
MARKER = "AMODEX_LUAU_OK checks=3 score=42"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly execute once in your authorized disposable test place")
    args = parser.parse_args()
    if args.live:
        names = ("AMODEX_ROBLOX_TEST_UNIVERSE_ID", "AMODEX_ROBLOX_TEST_PLACE_ID", "AMODEX_ROBLOX_OPEN_CLOUD_API_KEY")
        if any(not os.environ.get(name) for name in names):
            print(json.dumps({"status": "LIVE_BLOCKED", "reason": "authorized_test_target_and_api_key_required"}))
            return 2
        secrets = SecretBroker(values={names[2]: os.environ[names[2]]}, granted_names=(names[2],))
        target = RobloxCloudTarget(os.environ[names[0]], os.environ[names[1]], os.environ.get("AMODEX_ROBLOX_TEST_VERSION_ID"))
        http = httpx.Client(timeout=10, follow_redirects=False)
        key = secrets.read(names[2])
    else:
        fixture = json.loads((FIXTURES / "open_cloud.json").read_text())
        states = iter(fixture["states"])
        pages = iter(fixture["log_pages"])
        def simulate(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/logs"):
                return httpx.Response(200, json=next(pages))
            state = next(states)
            body = {"path": fixture["path"], "state": state}
            if state == "COMPLETE":
                body["output"] = fixture["output"]
            return httpx.Response(200, json=body)
        target = RobloxCloudTarget("123", "456", "7")
        http = httpx.Client(transport=httpx.MockTransport(simulate))
        key = "synthetic-local-key"
    try:
        with http, RobloxLuauClient(target=target, api_key=key, http=http,
                                  **({} if args.live else {"sleep": lambda _: None})) as client:
            task = client.wait(client.submit((FIXTURES / "score_test.luau").read_text()))
            logs = client.collect_logs(task.path)
            verified = task.state == "COMPLETE" and task.results == (EXPECTED,) and MARKER in logs
            print(json.dumps({"status": ("LIVE_PASS" if args.live else "SIMULATED_PASS") if verified else "VALIDATION_FAILED",
                              "remote_executed": args.live, "state": task.state,
                              "result_verified": verified, "logs_verified": MARKER in logs}))
            return 0 if verified else 1
    except RobloxCloudError as exc:
        print(json.dumps({"status": "UNKNOWN_OUTCOME" if exc.ambiguous_outcome else "BLOCKED",
                          "code": exc.code, "resubmit": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
