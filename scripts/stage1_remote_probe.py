#!/usr/bin/env python3
"""Real Stage 1 Cloudflare + Neon + Gemini vertical-slice probe.

Creates only namespaced temporary resources, deploys the actual Python Stage 1
API, writes runs through the remote API, processes them with the production
Stage1Worker against the same Neon database, redeploys the control plane, proves
state survives, proves free-quota exhaustion waits instead of spending, then
removes the temporary resources.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from amodex.storage.postgres import PostgresRunRepository

NEON_API = "https://console.neon.tech/api/v2"
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
DEPLOY_DIR = Path("deploy/cloudflare").resolve()
PREFIX = "amodex-s1-"


class ProbeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def request_json(method: str, url: str, *, headers: dict[str, str] | None = None,
                 payload: dict[str, Any] | None = None, timeout: int = 45,
                 allow: set[int] | None = None) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if payload is not None:
        req.add_header("Content-Type", "application/json")
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
        raise ProbeFailure(f"{method} {url} -> HTTP {exc.code}") from exc


def neon_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": "amodex-stage1"}


def cf_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "amodex-stage1"}


def resolve_neon_org(key: str) -> str:
    _, body = request_json("GET", f"{NEON_API}/users/me/organizations", headers=neon_headers(key))
    organizations = body.get("organizations") if isinstance(body, dict) else None
    require(isinstance(organizations, list), "Neon organizations unavailable")
    ids = [str(item["id"]) for item in organizations if isinstance(item, dict) and item.get("id")]
    require(len(ids) == 1, "expected exactly one Neon organization")
    return ids[0]


def create_neon_project(key: str, org_id: str, name: str) -> tuple[str, str]:
    status, body = request_json("POST", f"{NEON_API}/projects", headers=neon_headers(key),
        payload={"project": {"name": name, "pg_version": 18, "org_id": org_id}})
    require(status == 201 and isinstance(body, dict), "Neon project creation failed")
    project = body.get("project")
    uris = body.get("connection_uris")
    require(isinstance(project, dict) and project.get("id"), "Neon project id missing")
    require(isinstance(uris, list) and uris, "Neon connection URI missing")
    uri = uris[0].get("connection_uri") if isinstance(uris[0], dict) else None
    require(isinstance(uri, str) and uri.startswith("postgres"), "invalid Neon connection URI")
    return str(project["id"]), uri


def delete_neon_project(key: str, project_id: str) -> bool:
    for attempt in range(8):
        status, _ = request_json("DELETE", f"{NEON_API}/projects/{project_id}", headers=neon_headers(key),
                                 allow={200, 204, 404, 423, 503})
        if status in {200, 204, 404}:
            return True
        time.sleep(min(2 ** attempt, 20))
    return False


def delete_worker(token: str, account_id: str, name: str) -> bool:
    encoded = urllib.parse.quote(name, safe="")
    status, _ = request_json("DELETE", f"{CLOUDFLARE_API}/accounts/{account_id}/workers/scripts/{encoded}",
                             headers=cf_headers(token), allow={200, 204, 404})
    return status in {200, 204, 404}


def run_command(args: list[str], *, env: dict[str, str], cwd: Path | None = None, timeout: int = 300) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if result.returncode != 0:
        raise ProbeFailure(f"command failed ({result.returncode}): {' '.join(args[:4])}\n{result.stdout[-4000:]}")
    return result.stdout


def deploy_worker(*, env: dict[str, str], name: str, build_id: str, secrets_file: Path) -> str:
    deploy_env = env.copy()
    deploy_env.pop("VIRTUAL_ENV", None)
    deploy_env["UV_PYTHON"] = "3.13.5"
    output = run_command([
        "uv", "run", "--project", str(DEPLOY_DIR), "--python", "3.13.5", "--frozen",
        "pywrangler", "deploy", "--name", name, "--var", f"BUILD_ID:{build_id}",
        "--secrets-file", str(secrets_file),
    ], env=deploy_env, cwd=DEPLOY_DIR, timeout=420)
    matches = re.findall(r"https://[A-Za-z0-9._-]+\.workers\.dev", output)
    return matches[-1].rstrip("/") if matches else ""


def resolve_worker_url(token: str, account_id: str, name: str, deployed_url: str) -> str:
    if deployed_url:
        return deployed_url
    _, body = request_json("GET", f"{CLOUDFLARE_API}/accounts/{account_id}/workers/subdomain", headers=cf_headers(token))
    result = body.get("result") if isinstance(body, dict) else None
    subdomain = result.get("subdomain") if isinstance(result, dict) else None
    require(isinstance(subdomain, str) and subdomain, "Cloudflare workers.dev subdomain unavailable")
    return f"https://{name}.{subdomain}.workers.dev"


def public_json(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    return request_json(method, url, headers={"Content-Type": "application/json", "User-Agent": "amodex-stage1-client"},
                        payload=payload, timeout=60, allow={400, 404, 500, 502, 503, 504})


def wait_build(url: str, build_id: str, timeout: int = 300) -> None:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            status, body = public_json("GET", f"{url}/__deployment")
            last = body
            if status == 200 and isinstance(body, dict) and body.get("build_id") == build_id:
                return
        except Exception as exc:
            last = str(exc)
        time.sleep(5)
    raise ProbeFailure(f"Worker build {build_id} did not become reachable: {last}")


def create_remote_run(url: str, prompt: str) -> dict[str, Any]:
    status, body = public_json("POST", f"{url}/v1/runs", {"schema_version": 1, "prompt": prompt})
    require(status == 201 and isinstance(body, dict) and body.get("run_id"), "remote run creation failed")
    return body


def get_remote_run(url: str, run_id: str) -> dict[str, Any]:
    status, body = public_json("GET", f"{url}/v1/runs/{run_id}")
    require(status == 200 and isinstance(body, dict), "remote run read failed")
    return body


def get_remote_events(url: str, run_id: str) -> list[dict[str, Any]]:
    status, body = public_json("GET", f"{url}/v1/runs/{run_id}/events")
    require(status == 200 and isinstance(body, list), "remote event read failed")
    return body


def run_worker(database_url: str, gemini_key: str, *, quota_available: bool) -> str:
    env = os.environ.copy()
    env["AMODEX_DATABASE_URL"] = database_url
    env["GEMINI_API_KEY"] = gemini_key
    env["AMODEX_FREE_QUOTA_AVAILABLE"] = "1" if quota_available else "0"
    return run_command([sys.executable, "-m", "amodex.runtime.worker"], env=env, timeout=120)


def run_probe() -> dict[str, Any]:
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN") or ""
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or ""
    neon_key = os.environ.get("NEON_API_KEY") or ""
    gemini_key = os.environ.get("GEMINI_API_KEY") or ""
    source_sha = os.environ.get("GITHUB_SHA") or ""
    run_id = os.environ.get("GITHUB_RUN_ID") or str(int(time.time()))
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    require(all((cf_token, account_id, neon_key, gemini_key)), "required Stage 1 cloud credentials are missing")
    require(len(source_sha) == 40, "GITHUB_SHA must be exact")

    suffix = re.sub(r"[^a-z0-9-]", "-", f"{run_id}-{attempt}".lower())[-28:]
    worker_name = f"{PREFIX}{suffix}"
    project_name = f"{PREFIX}{suffix}"
    build_a = f"{source_sha[:12]}-a"
    build_b = f"{source_sha[:12]}-b"
    runner_temp = Path(os.environ.get("RUNNER_TEMP", "/tmp"))
    secrets_file = runner_temp / f"amodex-s1-secrets-{suffix}.json"
    project_id: str | None = None
    worker_created = False
    cleanup = {"worker_deleted": False, "neon_project_deleted": False}
    command_env = os.environ.copy()
    command_env["WRANGLER_SEND_METRICS"] = "false"

    try:
        org_id = resolve_neon_org(neon_key)
        project_id, database_url = create_neon_project(neon_key, org_id, project_name)
        repo = PostgresRunRepository(database_url)
        applied = repo.migrate_all(Path("migrations"))
        require(applied == ("0001_stage1_runtime.sql", "0002_provider_request_intent.sql"), f"unexpected migrations: {applied}")

        secrets_file.write_text(json.dumps({"DATABASE_URL": database_url}), encoding="utf-8")
        secrets_file.chmod(0o600)

        deploy_url = deploy_worker(env=command_env, name=worker_name, build_id=build_a, secrets_file=secrets_file)
        worker_created = True
        worker_url = resolve_worker_url(cf_token, account_id, worker_name, deploy_url)
        wait_build(worker_url, build_a)

        created = create_remote_run(worker_url, "Reply with AMODEX_STAGE1_REMOTE_OK in one short sentence.")
        first_run_id = str(created["run_id"])
        require(created.get("state") == "QUEUED", "remote API did not persist QUEUED state")

        # The POST connection is gone here. A separate worker process claims the remote lease.
        run_worker(database_url, gemini_key, quota_available=True)
        completed_a = get_remote_run(worker_url, first_run_id)
        require(completed_a.get("state") == "COMPLETED", f"real remote provider path did not complete: {completed_a.get('state')}")
        require(completed_a.get("provider") == "gemini", "remote result provider mismatch")
        require(bool(completed_a.get("result_text")), "remote result text missing")
        events_a = get_remote_events(worker_url, first_run_id)
        require(events_a and events_a[-1].get("kind") == "RUN_COMPLETED", "durable completion event missing")

        deploy_worker(env=command_env, name=worker_name, build_id=build_b, secrets_file=secrets_file)
        wait_build(worker_url, build_b)
        completed_b = get_remote_run(worker_url, first_run_id)
        require(completed_b.get("state") == "COMPLETED", "run state lost across control-plane redeploy")
        require(completed_b.get("result_text") == completed_a.get("result_text"), "durable result changed across redeploy")

        quota_run = create_remote_run(worker_url, "This run must wait when free quota is unavailable.")
        quota_run_id = str(quota_run["run_id"])
        run_worker(database_url, gemini_key, quota_available=False)
        waiting = get_remote_run(worker_url, quota_run_id)
        require(waiting.get("state") == "WAITING_PROVIDER", "quota exhaustion did not enter WAITING_PROVIDER")
        require(waiting.get("last_error_code") == "FREE_QUOTA_UNAVAILABLE", "quota wait reason mismatch")
        require(waiting.get("lease_owner") is None, "quota-waiting run retained worker lease")

        serialized = json.dumps({"completed": completed_b, "events": events_a, "waiting": waiting}, sort_keys=True)
        require(gemini_key not in serialized and database_url not in serialized and neon_key not in serialized and cf_token not in serialized,
                "secret leaked into API/event payload")

        cleanup["worker_deleted"] = delete_worker(cf_token, account_id, worker_name)
        worker_created = not cleanup["worker_deleted"]
        cleanup["neon_project_deleted"] = delete_neon_project(neon_key, project_id)
        if cleanup["neon_project_deleted"]:
            project_id = None
        require(all(cleanup.values()), f"cleanup incomplete: {cleanup}")

        return {
            "status": "PASS",
            "source_sha": source_sha,
            "backend": "cloudflare-python-worker+neon-postgres+github-actions-runner",
            "worker_name": worker_name,
            "neon_project_name": project_name,
            "migrations": list(applied),
            "build_a": build_a,
            "build_b": build_b,
            "client_disconnect_reconnect": True,
            "real_gemini_completed": True,
            "control_plane_redeploy_preserved_state": True,
            "free_quota_waited_without_paid_fallback": True,
            "cleanup": cleanup,
        }
    finally:
        try:
            secrets_file.unlink(missing_ok=True)
        except Exception:
            pass
        if worker_created:
            cleanup["worker_deleted"] = delete_worker(cf_token, account_id, worker_name)
        if project_id:
            cleanup["neon_project_deleted"] = delete_neon_project(neon_key, project_id)


if __name__ == "__main__":
    try:
        print(json.dumps(run_probe(), indent=2, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)[:1000]}, indent=2), file=sys.stderr)
        raise
