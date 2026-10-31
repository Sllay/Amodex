#!/usr/bin/env python3
"""Delete only orphaned P0-06B Cloudflare/Neon resources.

Safety invariant: this script may mutate only resources whose visible name starts
with the exact prefix ``amodex-p006b-``. It never deletes unprefixed resources.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PREFIX = "amodex-p006b-"
CF_API = "https://api.cloudflare.com/client/v4"
NEON_API = "https://console.neon.tech/api/v2"


class CleanupFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CleanupFailure(message)


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    allow: set[int] | None = None,
    timeout: int = 45,
) -> tuple[int, Any]:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return int(response.status), json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = None
        if allow and exc.code in allow:
            return int(exc.code), body
        raise CleanupFailure(f"{method} {url} -> {exc.code}: request failed") from exc


def cf_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "amodex-p0-06b-cleanup",
    }


def neon_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "amodex-p0-06b-cleanup",
    }


def cloudflare_worker_names(token: str, account_id: str) -> list[str]:
    _, body = request_json(
        "GET",
        f"{CF_API}/accounts/{account_id}/workers/services",
        headers=cf_headers(token),
    )
    require(isinstance(body, dict) and body.get("success") is True, "Cloudflare service listing failed")
    result = body.get("result")
    require(isinstance(result, list), "Cloudflare service list missing")
    names: list[str] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("id")
        if isinstance(name, str):
            names.append(name)
    return names


def delete_cloudflare_worker(token: str, account_id: str, name: str) -> None:
    require(name.startswith(PREFIX), "refusing Cloudflare delete outside P0-06B prefix")
    encoded = urllib.parse.quote(name, safe="")
    status, body = request_json(
        "DELETE",
        f"{CF_API}/accounts/{account_id}/workers/scripts/{encoded}",
        headers=cf_headers(token),
        allow={200, 204, 404},
    )
    require(status in {200, 204, 404}, f"Cloudflare delete failed for namespaced resource: {status}")
    if isinstance(body, dict) and status != 404:
        require(body.get("success", True) is True, "Cloudflare delete response was not successful")


def neon_projects(api_key: str) -> list[dict[str, str]]:
    _, body = request_json("GET", f"{NEON_API}/projects", headers=neon_headers(api_key))
    projects = body.get("projects") if isinstance(body, dict) else None
    require(isinstance(projects, list), "Neon project list missing")
    result: list[dict[str, str]] = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        project_id = item.get("id")
        if isinstance(name, str) and isinstance(project_id, str):
            result.append({"name": name, "id": project_id})
    return result


def delete_neon_project(api_key: str, project_id: str, project_name: str) -> None:
    require(project_name.startswith(PREFIX), "refusing Neon delete outside P0-06B prefix")
    for attempt in range(8):
        status, _ = request_json(
            "DELETE",
            f"{NEON_API}/projects/{urllib.parse.quote(project_id, safe='')}",
            headers=neon_headers(api_key),
            allow={200, 204, 404, 423, 503},
        )
        if status in {200, 204, 404}:
            return
        time.sleep(min(2**attempt, 20))
    raise CleanupFailure("Neon namespaced project did not delete after retries")


def main() -> int:
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    neon_key = os.environ.get("NEON_API_KEY")
    require(bool(cf_token and account_id and neon_key), "required cleanup credentials missing")

    workers_before = [name for name in cloudflare_worker_names(cf_token, account_id) if name.startswith(PREFIX)]
    projects_before = [project for project in neon_projects(neon_key) if project["name"].startswith(PREFIX)]

    for name in workers_before:
        delete_cloudflare_worker(cf_token, account_id, name)
    for project in projects_before:
        delete_neon_project(neon_key, project["id"], project["name"])

    workers_after: list[str] = []
    projects_after: list[dict[str, str]] = []
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        workers_after = [name for name in cloudflare_worker_names(cf_token, account_id) if name.startswith(PREFIX)]
        projects_after = [project for project in neon_projects(neon_key) if project["name"].startswith(PREFIX)]
        if not workers_after and not projects_after:
            break
        time.sleep(5)

    require(not workers_after, f"Cloudflare P0-06B leftovers remain: {len(workers_after)}")
    require(not projects_after, f"Neon P0-06B leftovers remain: {len(projects_after)}")

    print(json.dumps({
        "status": "PASS",
        "prefix": PREFIX,
        "cloudflare": {
            "found": len(workers_before),
            "deleted": len(workers_before),
            "remaining": 0,
        },
        "neon": {
            "found": len(projects_before),
            "deleted": len(projects_before),
            "remaining": 0,
        },
        "non_prefixed_resources_touched": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
