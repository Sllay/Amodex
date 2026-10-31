#!/usr/bin/env python3
"""P0-06B real Cloudflare Workers + Neon Postgres deployment probe.

Research-only. Creates one namespaced Neon project and one namespaced Cloudflare
Worker, proves durable state across a Worker redeploy and a Cron Trigger, then
deletes the temporary resources. No credential or database URI is emitted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

NEON_API = "https://console.neon.tech/api/v2"
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
WORKER_DIR = Path(__file__).resolve().parent / "worker"
CRON_EXPRESSION = "* * * * *"
CRON_PROPAGATION_TIMEOUT_SECONDS = 1020


class ProbeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 45,
    allow: set[int] | None = None,
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
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
        raise ProbeFailure(f"{method} {url} -> {exc.code}: {body}") from exc


def neon_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "amodex-p0-06b-deploy",
    }


def cf_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "amodex-p0-06b-deploy",
    }


def resolve_neon_org(api_key: str, configured_org: str | None) -> str:
    if configured_org:
        return configured_org
    _, body = request_json(
        "GET",
        f"{NEON_API}/users/me/organizations",
        headers=neon_headers(api_key),
    )
    organizations = body.get("organizations") if isinstance(body, dict) else None
    require(isinstance(organizations, list), "Neon organization list missing")
    ids = [str(item.get("id")) for item in organizations if isinstance(item, dict) and item.get("id")]
    require(len(ids) == 1, "Neon account must expose exactly one organization or set NEON_ORG_ID")
    return ids[0]


def create_neon_project(api_key: str, org_id: str, name: str) -> tuple[str, str]:
    status, body = request_json(
        "POST",
        f"{NEON_API}/projects",
        headers=neon_headers(api_key),
        payload={"project": {"name": name, "pg_version": 18, "org_id": org_id}},
    )
    require(status == 201 and isinstance(body, dict), "Neon project creation failed")
    project = body.get("project")
    uris = body.get("connection_uris")
    require(isinstance(project, dict) and project.get("id"), "Neon project id missing")
    require(isinstance(uris, list) and uris, "Neon connection URI missing")
    connection_uri = uris[0].get("connection_uri") if isinstance(uris[0], dict) else None
    require(isinstance(connection_uri, str) and connection_uri.startswith("postgres"), "invalid Neon connection URI")
    return str(project["id"]), connection_uri


def delete_neon_project(api_key: str, project_id: str) -> bool:
    for attempt in range(8):
        status, _ = request_json(
            "DELETE",
            f"{NEON_API}/projects/{project_id}",
            headers=neon_headers(api_key),
            allow={200, 204, 404, 423, 503},
        )
        if status in {200, 204, 404}:
            return True
        time.sleep(min(2**attempt, 20))
    return False


def delete_worker(token: str, account_id: str, worker_name: str) -> bool:
    encoded = urllib.parse.quote(worker_name, safe="")
    status, body = request_json(
        "DELETE",
        f"{CLOUDFLARE_API}/accounts/{account_id}/workers/scripts/{encoded}",
        headers=cf_headers(token),
        allow={200, 204, 404},
    )
    if status == 404:
        return True
    return status in {200, 204} and (not isinstance(body, dict) or body.get("success", True) is True)


def run_command(args: list[str], env: dict[str, str]) -> str:
    result = subprocess.run(
        args,
        cwd=WORKER_DIR,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise ProbeFailure(f"command failed ({result.returncode}): {' '.join(args[:3])}\n{result.stdout[-6000:]}")
    return result.stdout


def install_worker_dependencies(env: dict[str, str]) -> dict[str, str]:
    run_command(["npm", "install", "--no-package-lock", "--no-audit", "--no-fund"], env)
    raw = run_command(["npm", "ls", "--json", "--depth=0"], env)
    parsed = json.loads(raw)
    dependencies = parsed.get("dependencies") or {}
    return {
        name: str(meta.get("version"))
        for name, meta in dependencies.items()
        if isinstance(meta, dict) and meta.get("version")
    }


def deploy_worker(
    env: dict[str, str],
    worker_name: str,
    build_id: str,
    schedule_marker: str,
    secrets_file: Path,
) -> str:
    output = run_command(
        [
            "npx",
            "wrangler",
            "deploy",
            "--name",
            worker_name,
            "--var",
            f"BUILD_ID:{build_id}",
            "--var",
            f"SCHEDULE_MARKER:{schedule_marker}",
            "--secrets-file",
            str(secrets_file),
        ],
        env,
    )
    matches = re.findall(r"https://[A-Za-z0-9._-]+\.workers\.dev", output)
    return matches[-1].rstrip("/") if matches else ""


def resolve_worker_url(token: str, account_id: str, worker_name: str, deploy_output_url: str) -> str:
    if deploy_output_url:
        return deploy_output_url
    _, body = request_json(
        "GET",
        f"{CLOUDFLARE_API}/accounts/{account_id}/workers/subdomain",
        headers=cf_headers(token),
    )
    result = body.get("result") if isinstance(body, dict) else None
    subdomain = result.get("subdomain") if isinstance(result, dict) else None
    require(isinstance(subdomain, str) and subdomain, "Cloudflare workers.dev subdomain unavailable")
    return f"https://{worker_name}.{subdomain}.workers.dev"


def public_json(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    return request_json(
        method,
        url,
        headers={"Content-Type": "application/json", "User-Agent": "amodex-p0-06b-client"},
        payload=payload,
        timeout=30,
        allow={400, 404, 500, 502, 503, 504},
    )


def wait_health(url: str, expected_build: str, timeout: int = 240) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            status, body = public_json("GET", f"{url}/health")
            last = body
            if (
                status == 200
                and isinstance(body, dict)
                and body.get("ok") is True
                and body.get("build_id") == expected_build
            ):
                return body
        except Exception as exc:
            last = str(exc)
        time.sleep(5)
    raise ProbeFailure(f"Worker did not become healthy for build {expected_build}: {last}")


def write_event(url: str, marker: str) -> dict[str, Any]:
    status, body = public_json("POST", f"{url}/event", {"marker": marker})
    require(status == 200 and isinstance(body, dict) and isinstance(body.get("event"), dict), "event write failed")
    return body["event"]


def read_event(url: str, marker: str) -> dict[str, Any] | None:
    status, body = public_json("GET", f"{url}/event?marker={urllib.parse.quote(marker, safe='')}")
    if status == 404:
        return None
    require(status == 200 and isinstance(body, dict), "event read failed")
    event = body.get("event")
    return event if isinstance(event, dict) else None


def get_worker_schedules(token: str, account_id: str, worker_name: str) -> list[str]:
    encoded = urllib.parse.quote(worker_name, safe="")
    _, body = request_json(
        "GET",
        f"{CLOUDFLARE_API}/accounts/{account_id}/workers/scripts/{encoded}/schedules",
        headers=cf_headers(token),
    )
    require(isinstance(body, dict) and body.get("success") is True, "Cloudflare schedule lookup failed")
    result = body.get("result")
    schedules = result.get("schedules") if isinstance(result, dict) else None
    require(isinstance(schedules, list), "Cloudflare schedule list missing")
    return [
        str(item.get("cron"))
        for item in schedules
        if isinstance(item, dict) and item.get("cron")
    ]


def wait_cron(
    url: str,
    marker: str,
    timeout: int = CRON_PROPAGATION_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        event = read_event(url, marker)
        if event:
            return event, round(time.monotonic() - started, 3)
        time.sleep(15)
    raise ProbeFailure(
        f"Cloudflare Cron Trigger did not persist its marker within {timeout}s propagation window"
    )


def run_probe(output: Path | None) -> dict[str, Any]:
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    neon_key = os.environ.get("NEON_API_KEY")
    neon_org = os.environ.get("NEON_ORG_ID")
    source_sha = os.environ.get("GITHUB_SHA") or ""
    run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")

    require(bool(cf_token and account_id and neon_key), "missing Cloudflare/Neon credentials")
    require(len(source_sha) == 40, "GITHUB_SHA must be an exact commit SHA")

    suffix = re.sub(r"[^a-z0-9-]", "-", f"{run_id}-{run_attempt}".lower())[-30:]
    worker_name = f"amodex-p006b-{suffix}"
    project_name = f"amodex-p006b-{suffix}"
    durable_marker = f"durable:{source_sha[:12]}:{suffix}"
    schedule_seed = secrets.token_hex(8)
    cron_marker = f"cron:{schedule_seed}"
    synthetic_secret = secrets.token_urlsafe(24)
    build_a = f"{source_sha[:12]}-a-{run_attempt}"
    build_b = f"{source_sha[:12]}-b-{run_attempt}"
    project_id: str | None = None
    worker_created = False
    cleanup = {"worker_deleted": False, "neon_project_deleted": False}
    started = time.monotonic()

    runner_temp = Path(os.environ.get("RUNNER_TEMP", "/tmp"))
    secrets_file = runner_temp / f"amodex-p006b-secrets-{suffix}.json"
    command_env = os.environ.copy()
    command_env["WRANGLER_SEND_METRICS"] = "false"

    try:
        org_id = resolve_neon_org(neon_key, neon_org)
        project_id, database_url = create_neon_project(neon_key, org_id, project_name)

        secrets_file.write_text(
            json.dumps({"DATABASE_URL": database_url, "AMODEX_P0_06B_SECRET": synthetic_secret}),
            encoding="utf-8",
        )
        secrets_file.chmod(0o600)

        versions = install_worker_dependencies(command_env)
        deploy_url = deploy_worker(command_env, worker_name, build_a, schedule_seed, secrets_file)
        worker_created = True
        worker_url = resolve_worker_url(cf_token, account_id, worker_name, deploy_url)

        schedules_a = get_worker_schedules(cf_token, account_id, worker_name)
        require(CRON_EXPRESSION in schedules_a, f"expected Cron schedule not attached after first deploy: {schedules_a}")

        health_a = wait_health(worker_url, build_a)
        first_event = write_event(worker_url, durable_marker)
        require(first_event.get("writer_build") == build_a, "initial durable marker writer mismatch")

        deploy_worker(command_env, worker_name, build_b, schedule_seed, secrets_file)
        schedules_b = get_worker_schedules(cf_token, account_id, worker_name)
        require(CRON_EXPRESSION in schedules_b, f"expected Cron schedule missing after redeploy: {schedules_b}")

        health_b = wait_health(worker_url, build_b)
        persisted = read_event(worker_url, durable_marker)
        require(isinstance(persisted, dict), "durable marker missing after redeploy")
        require(persisted.get("writer_build") == build_a, "durable marker changed after redeploy")

        cron_event, cron_wait_seconds = wait_cron(worker_url, cron_marker)
        require(cron_event.get("writer_build") in {build_a, build_b}, "Cron marker writer invalid")

        require(health_a.get("database") is True and health_b.get("database") is True, "database health failed")
        require(health_a.get("secret_injected") is True and health_b.get("secret_injected") is True, "secret injection failed")
        require(health_a.get("provider_egress") is True and health_b.get("provider_egress") is True, "provider egress failed")
        require(health_a.get("secret_fingerprint") == health_b.get("secret_fingerprint"), "secret fingerprint changed across redeploy")

        cleanup["worker_deleted"] = delete_worker(cf_token, account_id, worker_name)
        worker_created = not cleanup["worker_deleted"]
        cleanup["neon_project_deleted"] = delete_neon_project(neon_key, project_id)
        if cleanup["neon_project_deleted"]:
            project_id = None
        require(all(cleanup.values()), f"cleanup incomplete: {cleanup}")

        result = {
            "status": "PASS",
            "source_sha": source_sha,
            "backend": "cloudflare-workers+neon-postgres",
            "worker_name": worker_name,
            "neon_project_name": project_name,
            "versions": versions,
            "build_ids": {"initial": build_a, "redeploy": build_b},
            "health": {
                "database": True,
                "secret_injected": True,
                "provider_egress": True,
            },
            "cron": {
                "expression": CRON_EXPRESSION,
                "schedule_attached_before_and_after_redeploy": True,
                "propagation_wait_seconds": cron_wait_seconds,
                "persisted_state": True,
            },
            "durable_state_survived_redeploy": True,
            "cron_trigger_persisted_state": True,
            "event_driven_compute": True,
            "client_connection_required_for_background_cron": False,
            "cleanup": cleanup,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "credential_values_recorded": False,
            "database_uri_recorded": False,
        }
        encoded = json.dumps(result, indent=2, sort_keys=True)
        print(encoded)
        if output:
            output.write_text(encoded + "\n", encoding="utf-8")
        return result
    finally:
        try:
            if secrets_file.exists():
                secrets_file.unlink()
        except Exception:
            pass
        try:
            shutil.rmtree(WORKER_DIR / "node_modules", ignore_errors=True)
        except Exception:
            pass
        if worker_created:
            try:
                delete_worker(cf_token, account_id, worker_name)
            except Exception:
                pass
        if project_id:
            try:
                delete_neon_project(neon_key, project_id)
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_probe(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
