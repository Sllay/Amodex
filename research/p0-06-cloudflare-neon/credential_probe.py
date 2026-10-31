#!/usr/bin/env python3
"""Read-only credential/capability gate for P0-06B.

No infrastructure mutation. Validates Cloudflare Workers account access and Neon API
access, then emits only non-sensitive metadata.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class ProbeFailure(RuntimeError):
    pass


def request_json(url: str, headers: dict[str, str], timeout: int = 30) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            return int(response.status), json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = None
        raise ProbeFailure(f"GET {url} -> {exc.code}: {parsed}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cloudflare-token", default=os.environ.get("CLOUDFLARE_API_TOKEN"))
    parser.add_argument("--cloudflare-account-id", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID"))
    parser.add_argument("--neon-api-key", default=os.environ.get("NEON_API_KEY"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    missing = [
        name
        for name, value in {
            "CLOUDFLARE_API_TOKEN": args.cloudflare_token,
            "CLOUDFLARE_ACCOUNT_ID": args.cloudflare_account_id,
            "NEON_API_KEY": args.neon_api_key,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit("missing required credentials: " + ", ".join(missing))

    cf_status, cf_body = request_json(
        f"https://api.cloudflare.com/client/v4/accounts/{args.cloudflare_account_id}/workers/services",
        {
            "Authorization": f"Bearer {args.cloudflare_token}",
            "Content-Type": "application/json",
            "User-Agent": "amodex-p0-06b",
        },
    )
    if not isinstance(cf_body, dict) or cf_body.get("success") is not True:
        raise ProbeFailure("Cloudflare Workers API did not return success=true")
    cf_result = cf_body.get("result")
    cf_services = cf_result if isinstance(cf_result, list) else []

    neon_status, neon_body = request_json(
        "https://console.neon.tech/api/v2/projects",
        {
            "Authorization": f"Bearer {args.neon_api_key}",
            "Accept": "application/json",
            "User-Agent": "amodex-p0-06b",
        },
    )
    if not isinstance(neon_body, dict):
        raise ProbeFailure("Neon projects API returned unexpected payload")
    projects = neon_body.get("projects")
    if not isinstance(projects, list):
        projects = []

    result = {
        "status": "PASS",
        "mutation_performed": False,
        "cloudflare": {
            "http_status": cf_status,
            "workers_api_access": True,
            "existing_worker_service_count": len(cf_services),
            "account_id_recorded": False,
            "token_recorded": False,
        },
        "neon": {
            "http_status": neon_status,
            "api_access": True,
            "existing_project_count": len(projects),
            "api_key_recorded": False,
        },
    }

    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
