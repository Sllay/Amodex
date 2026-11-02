#!/usr/bin/env python3
"""Read-only remote activation smoke for Render/Web/Termux.

Environment:
  AMODEX_URL                 Base HTTPS URL (unless --url is supplied).
  AMODEX_ACCESS_PASSCODE     Optional private access passcode.
  AMODEX_EXPECT_SHA          Optional expected deployed Git SHA/prefix.

The script never prints the passcode or provider credentials.
"""

from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


class RemoteSession:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def json_request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        data = None
        headers = {"Accept": "application/json", "User-Agent": "amodex-activation-smoke/1"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read()
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                return int(response.status), payload
        except HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                payload = {}
            return int(exc.code), payload

    def login(self, passcode: str) -> None:
        data = urlencode({"passcode": passcode}).encode("utf-8")
        request = Request(
            self.base_url + "/auth/passcode",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html",
                "User-Agent": "amodex-activation-smoke/1",
            },
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                if int(response.status) not in {200, 303}:
                    raise RuntimeError(f"private access login failed: HTTP {response.status}")
        except HTTPError as exc:
            raise RuntimeError(f"private access login failed: HTTP {exc.code}") from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _unready_detail(system: dict[str, Any]) -> str:
    database = system.get("database", {}) if isinstance(system.get("database"), dict) else {}
    zero_cost = system.get("zero_cost", {}) if isinstance(system.get("zero_cost"), dict) else {}
    safe = {
        "database_reason": database.get("reason", "unknown"),
        "database_reachable": database.get("reachable"),
        "schema_compatible": database.get("schema_compatible"),
        "missing_migrations": database.get("missing_migrations"),
        "checksum_mismatches": database.get("checksum_mismatches"),
        "zero_cost_ready": zero_cost.get("ready"),
        "zero_cost_violations": zero_cost.get("violations", ()),
    }
    return json.dumps(safe, ensure_ascii=False, sort_keys=True)


def run_smoke(
    *,
    base_url: str,
    passcode: str | None,
    expect_sha: str | None,
    require_benchmarks: bool,
) -> dict[str, Any]:
    session = RemoteSession(base_url)

    health_status, health = session.json_request("/healthz")
    _require(health_status == 200 and health.get("status") == "ok", "liveness check failed")

    ready_status, readiness = session.json_request("/readyz")
    _require(ready_status == 200, f"readiness check failed: HTTP {ready_status}")
    _require(readiness.get("ready") is True, "deployment is not ready")
    _require(readiness.get("zero_cost", {}).get("ready") is True, "zero-cost policy is not ready")

    deployed_sha = str(readiness.get("release", {}).get("git_sha", "unknown"))
    if expect_sha:
        expected = expect_sha.strip().lower()
        actual = deployed_sha.strip().lower()
        _require(
            actual != "unknown" and (actual.startswith(expected) or expected.startswith(actual)),
            f"deployed SHA mismatch: expected {expected[:12]}, got {actual[:12]}",
        )

    status_code, system = session.json_request("/v1/activation/status")
    if status_code == 401:
        _require(bool(passcode), "private deployment requires AMODEX_ACCESS_PASSCODE for full smoke")
        assert passcode is not None
        session.login(passcode)
        status_code, system = session.json_request("/v1/activation/status")
    _require(status_code == 200, f"authenticated activation status failed: HTTP {status_code}")
    _require(
        system.get("ready") is True,
        "authenticated activation status reports unready: " + _unready_detail(system),
    )

    inference = str(
        system.get("model_intelligence", {}).get("automatic_chat_inference", "UNAVAILABLE")
    )
    if require_benchmarks:
        _require(inference == "READY", f"model benchmark gate is not ready: {inference}")

    return {
        "status": "ok",
        "url": base_url,
        "release": readiness.get("release", {}),
        "database": readiness.get("database", {}),
        "zero_cost": readiness.get("zero_cost", {}),
        "automatic_chat_inference": inference,
        "benchmark_required": inference != "READY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Amodex remote activation smoke")
    parser.add_argument("--url", default=os.environ.get("AMODEX_URL", ""))
    parser.add_argument("--expect-sha", default=os.environ.get("AMODEX_EXPECT_SHA", ""))
    parser.add_argument("--require-benchmarks", action="store_true")
    args = parser.parse_args()

    base_url = args.url.strip().rstrip("/")
    if not base_url:
        parser.error("--url or AMODEX_URL is required")

    passcode = os.environ.get("AMODEX_ACCESS_PASSCODE", "").strip() or None
    try:
        result = run_smoke(
            base_url=base_url,
            passcode=passcode,
            expect_sha=args.expect_sha.strip() or None,
            require_benchmarks=bool(args.require_benchmarks),
        )
    except (RuntimeError, URLError) as exc:
        print(f"ACTIVATION_SMOKE_FAILED {exc}", file=sys.stderr)
        return 1

    print("ACTIVATION_SMOKE_OK " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
