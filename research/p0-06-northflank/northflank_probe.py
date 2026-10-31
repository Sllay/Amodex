#!/usr/bin/env python3
"""P0-06 Northflank minimal Control Plane feasibility probe.

Research-only. Creates a minimal project resource set through the official
Northflank API, proves durable PostgreSQL-backed state across restart/redeploy,
records CPU/RAM metrics, then cleans PoC resources unless explicitly retained.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.northflank.com/v1"
PROJECT_NAME = "Amodex Stage0"
ADDON_NAME = "amodex-p006-db"
SECRET_NAME = "Amodex P006 Runtime"
SERVICE_NAME = "amodex-p006-control-plane"
DEPLOYMENT_PLAN = "nf-compute-20"
BUILD_PLAN = "nf-compute-400-16"


class ProbeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


class Northflank:
    def __init__(self, token: str):
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int = 60,
        allow: set[int] | None = None,
    ) -> tuple[int, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{API}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "amodex-p0-06")
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
            if allow and exc.code in allow:
                return int(exc.code), parsed
            message = None
            if isinstance(parsed, dict):
                message = parsed.get("message") or parsed.get("error")
            raise ProbeFailure(
                f"Northflank API {method} {path} -> {exc.code}: {message or 'request failed'}"
            ) from exc


def public_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = 30,
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "amodex-p0-06")
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
        return int(exc.code), parsed


def data_of(body: Any) -> Any:
    return body.get("data") if isinstance(body, dict) else None


def list_items(nf: Northflank, path: str, key: str) -> list[dict[str, Any]]:
    _, body = nf.request("GET", path)
    data = data_of(body)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def find_named(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("name") == name or item.get("id") == name:
            return item
    return None


def ensure_project(nf: Northflank, region: str, configured_id: str | None) -> tuple[str, bool]:
    if configured_id:
        _, body = nf.request("GET", f"/projects/{configured_id}")
        project = data_of(body)
        require(isinstance(project, dict), "configured Northflank project not readable")
        return str(project.get("id") or configured_id), False

    projects = list_items(nf, "/projects", "projects")
    existing = find_named(projects, PROJECT_NAME)
    if existing:
        return str(existing["id"]), False

    _, body = nf.request(
        "PUT",
        "/projects",
        {
            "name": PROJECT_NAME,
            "description": "Amodex Stage 0 Northflank feasibility fixture",
            "region": region,
        },
    )
    project = data_of(body)
    require(isinstance(project, dict) and project.get("id"), "project creation returned no id")
    return str(project["id"]), True


def wait_addon_running(nf: Northflank, project_id: str, addon_id: str, timeout: int = 420) -> dict:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        _, body = nf.request("GET", f"/projects/{project_id}/addons/{addon_id}")
        addon = data_of(body)
        if isinstance(addon, dict):
            last = addon
            status = str(addon.get("status") or "")
            if status == "running":
                return addon
            if status in {"failed", "error", "errorAllocating", "deleted"}:
                raise ProbeFailure(f"addon entered terminal state: {status}")
        time.sleep(5)
    raise ProbeFailure(f"addon did not become running: {last.get('status')}")


def ensure_addon(nf: Northflank, project_id: str) -> tuple[str, bool]:
    addons = list_items(nf, f"/projects/{project_id}/addons", "addons")
    existing = find_named(addons, ADDON_NAME)
    if existing:
        addon_id = str(existing["id"])
        wait_addon_running(nf, project_id, addon_id)
        return addon_id, False

    _, body = nf.request(
        "POST",
        f"/projects/{project_id}/addons",
        {
            "name": ADDON_NAME,
            "description": "Amodex P0-06 durable authority store",
            "type": "postgresql",
            "version": "16-latest",
            "billing": {
                "deploymentPlan": DEPLOYMENT_PLAN,
                "storageClass": "nvme",
                "storage": 6144,
                "replicas": 1,
            },
            "tlsEnabled": False,
            "externalAccessEnabled": False,
            "ipPolicies": [],
            "pitrEnabled": False,
            "typeSpecificSettings": {},
        },
    )
    addon = data_of(body)
    require(isinstance(addon, dict) and addon.get("id"), "addon creation returned no id")
    addon_id = str(addon["id"])
    wait_addon_running(nf, project_id, addon_id)
    return addon_id, True


def postgres_uri(nf: Northflank, project_id: str, addon_id: str) -> str:
    _, body = nf.request("GET", f"/projects/{project_id}/addons/{addon_id}/credentials")
    credentials = data_of(body)
    require(isinstance(credentials, dict), "addon credentials missing")
    envs = credentials.get("envs") or {}
    require(isinstance(envs, dict), "addon credential envs missing")
    for key in ("POSTGRES_URI", "DATABASE_URL"):
        value = envs.get(key)
        if isinstance(value, str) and value.startswith("postgres"):
            return value
    for key, value in envs.items():
        if (
            isinstance(key, str)
            and "POSTGRES_URI" in key.upper()
            and "ADMIN" not in key.upper()
            and isinstance(value, str)
            and value.startswith("postgres")
        ):
            return value
    raise ProbeFailure(f"no standard PostgreSQL URI returned; safe keys={sorted(map(str, envs.keys()))}")


def put_secret_group(
    nf: Northflank,
    project_id: str,
    database_url: str,
    sentinel: str,
) -> str:
    _, body = nf.request(
        "PUT",
        f"/projects/{project_id}/secrets",
        {
            "name": SECRET_NAME,
            "description": "Amodex P0-06 runtime-only secrets",
            "type": "secret",
            "secretType": "environment",
            "priority": 10,
            "restrictions": {"restricted": False},
            "secrets": {
                "variables": {
                    "DATABASE_URL": database_url,
                    "AMODEX_P0_06_SECRET": sentinel,
                }
            },
        },
    )
    secret = data_of(body)
    require(isinstance(secret, dict) and secret.get("id"), "secret group returned no id")
    return str(secret["id"])


def service_payload(source_sha: str) -> dict[str, Any]:
    bundle_url = f"https://github.com/Sllay/Amodex/archive/{source_sha}.tar.gz"
    return {
        "name": SERVICE_NAME,
        "description": "Amodex P0-06 minimal Control Plane",
        "billing": {
            "deploymentPlan": DEPLOYMENT_PLAN,
            "buildPlan": BUILD_PLAN,
        },
        "deployment": {
            "instances": 1,
            "docker": {"configType": "default"},
            "storage": {"ephemeralStorage": {"storageSize": 1024}},
        },
        "ports": [
            {
                "name": "http",
                "internalPort": 8080,
                "public": True,
                "vpcAccessible": False,
                "protocol": "HTTP",
            }
        ],
        "buildSource": "bundle",
        "bundleData": {
            "bundleUrl": bundle_url,
            "branch": "stage0-p0-06",
        },
        "buildSettings": {
            "storage": {"ephemeralStorage": {"storageSize": 16384}},
            "dockerfile": {
                "buildEngine": "buildkit",
                "dockerFilePath": "/research/p0-06-northflank/Dockerfile",
                "dockerWorkDir": "/",
                "buildkit": {"useCache": True, "cacheStorageSize": 32768},
            },
        },
        "healthChecks": [
            {
                "protocol": "HTTP",
                "type": "readinessProbe",
                "path": "/health",
                "port": 8080,
                "initialDelaySeconds": 5,
                "periodSeconds": 10,
                "timeoutSeconds": 5,
                "failureThreshold": 12,
                "successThreshold": 1,
            }
        ],
        "createOptions": {"expiryTime": 7200},
    }


def ensure_service(nf: Northflank, project_id: str, source_sha: str) -> tuple[str, bool]:
    services = list_items(nf, f"/projects/{project_id}/services", "services")
    existing = find_named(services, SERVICE_NAME)
    if existing:
        service_id = str(existing["id"])
        nf.request("DELETE", f"/projects/{project_id}/services/{service_id}", allow={200, 202, 204, 404})
        time.sleep(3)

    _, body = nf.request(
        "PUT",
        f"/projects/{project_id}/services/combined",
        service_payload(source_sha),
    )
    service = data_of(body)
    require(isinstance(service, dict) and service.get("id"), "combined service returned no id")
    return str(service["id"]), True


def get_public_url(nf: Northflank, project_id: str, service_id: str) -> str | None:
    _, body = nf.request("GET", f"/projects/{project_id}/services/{service_id}/ports")
    data = data_of(body)
    ports = data.get("ports") if isinstance(data, dict) else None
    if not isinstance(ports, list):
        return None
    for port in ports:
        if isinstance(port, dict) and port.get("public") and port.get("dns"):
            return f"https://{port['dns']}"
    return None


def wait_health(
    nf: Northflank,
    project_id: str,
    service_id: str,
    *,
    timeout: int = 720,
    instance_must_differ: str | None = None,
) -> tuple[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        url = get_public_url(nf, project_id, service_id)
        if url:
            try:
                status, body = public_request("GET", f"{url}/health", timeout=15)
                last = body
                if status == 200 and isinstance(body, dict) and body.get("ok") is True:
                    if instance_must_differ and body.get("instance_id") == instance_must_differ:
                        time.sleep(5)
                        continue
                    return url, body
            except Exception as exc:
                last = repr(exc)
        time.sleep(5)
    raise ProbeFailure(f"service health did not become ready: {last}")


def write_marker(url: str, marker: str) -> dict[str, Any]:
    status, body = public_request(
        "POST",
        f"{url}/event",
        {"marker": marker, "payload": {"proof": "AMODEX_P0_06_DURABLE"}},
    )
    require(status == 200 and isinstance(body, dict), f"durable event write failed: {status}")
    return body


def read_marker(url: str, marker: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"marker": marker})
    status, body = public_request("GET", f"{url}/event?{query}")
    require(status == 200 and isinstance(body, dict), f"durable event read failed: {status}")
    event = body.get("event")
    require(isinstance(event, dict) and event.get("marker") == marker, "durable marker missing")
    return event


def start_rebuild(nf: Northflank, project_id: str, service_id: str, source_sha: str) -> None:
    nf.request(
        "POST",
        f"/projects/{project_id}/services/{service_id}/build",
        {
            "bundleUrl": f"https://github.com/Sllay/Amodex/archive/{source_sha}.tar.gz",
            "branch": "stage0-p0-06",
            "sha": source_sha,
        },
    )


def metrics(nf: Northflank, project_id: str, service_id: str) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        [
            ("queryType", "range"),
            ("duration", "120"),
            ("metricTypes", "cpu"),
            ("metricTypes", "memory"),
        ]
    )
    _, body = nf.request("GET", f"/projects/{project_id}/services/{service_id}/metrics?{query}")
    data = data_of(body)
    return data if isinstance(data, dict) else {}


def metric_values(metric: Any) -> list[float]:
    values: list[float] = []
    if not isinstance(metric, dict):
        return values
    for container in metric.get("values") or []:
        if not isinstance(container, dict):
            continue
        for point in container.get("data") or []:
            if isinstance(point, dict) and isinstance(point.get("value"), (int, float)):
                values.append(float(point["value"]))
    return values


def verify_plans(nf: Northflank) -> dict[str, Any]:
    _, body = nf.request("GET", "/plans")
    data = data_of(body)
    plans = data.get("plans") if isinstance(data, dict) else None
    require(isinstance(plans, list), "Northflank plans list unavailable")
    indexed = {str(p.get("id")): p for p in plans if isinstance(p, dict) and p.get("id")}
    require(DEPLOYMENT_PLAN in indexed, f"deployment plan unavailable: {DEPLOYMENT_PLAN}")
    require(BUILD_PLAN in indexed, f"build plan unavailable: {BUILD_PLAN}")
    return {
        "deployment": {
            "id": DEPLOYMENT_PLAN,
            "vcpus": indexed[DEPLOYMENT_PLAN].get("cpuResource"),
            "ram_mb": indexed[DEPLOYMENT_PLAN].get("ramResource"),
            "amount_per_month": indexed[DEPLOYMENT_PLAN].get("amountPerMonth"),
        },
        "build": {
            "id": BUILD_PLAN,
            "vcpus": indexed[BUILD_PLAN].get("cpuResource"),
            "ram_mb": indexed[BUILD_PLAN].get("ramResource"),
            "amount_per_month": indexed[BUILD_PLAN].get("amountPerMonth"),
        },
    }


def delete_resource(nf: Northflank, path: str) -> None:
    try:
        nf.request("DELETE", path, allow={200, 202, 204, 404, 409, 422})
    except Exception:
        pass


def run_probe(
    token: str,
    source_sha: str,
    region: str,
    configured_project: str | None,
    keep_resources: bool,
) -> dict[str, Any]:
    nf = Northflank(token)
    plan_observation = verify_plans(nf)
    project_id, project_created = ensure_project(nf, region, configured_project)
    addon_id: str | None = None
    secret_id: str | None = None
    service_id: str | None = None
    cleanup: dict[str, Any] = {}
    started = time.monotonic()

    try:
        addon_id, addon_created = ensure_addon(nf, project_id)
        uri = postgres_uri(nf, project_id, addon_id)
        sentinel = secrets.token_urlsafe(24)
        secret_id = put_secret_group(nf, project_id, uri, sentinel)
        service_id, _ = ensure_service(nf, project_id, source_sha)

        startup_begin = time.monotonic()
        url, first_health = wait_health(nf, project_id, service_id)
        startup_seconds = round(time.monotonic() - startup_begin, 3)
        require(first_health.get("database") is True, "service cannot reach PostgreSQL")
        require(first_health.get("secret_injected") is True, "runtime secret not injected")
        require(first_health.get("provider_egress") is True, "provider outbound egress failed")
        first_instance = str(first_health["instance_id"])

        marker = f"p0-06-{source_sha[:12]}"
        write = write_marker(url, marker)
        before_event = read_marker(url, marker)
        original_writer = str(before_event.get("writer_instance"))
        require(original_writer == first_instance, "durable event writer differs from first instance")

        nf.request("POST", f"/projects/{project_id}/services/{service_id}/restart", {})
        _, restart_health = wait_health(
            nf,
            project_id,
            service_id,
            instance_must_differ=first_instance,
        )
        restart_instance = str(restart_health["instance_id"])
        restart_event = read_marker(url, marker)
        require(
            str(restart_event.get("writer_instance")) == original_writer,
            "database state changed/lost across service restart",
        )

        start_rebuild(nf, project_id, service_id, source_sha)
        _, redeploy_health = wait_health(
            nf,
            project_id,
            service_id,
            timeout=900,
            instance_must_differ=restart_instance,
        )
        redeploy_instance = str(redeploy_health["instance_id"])
        redeploy_event = read_marker(url, marker)
        require(
            str(redeploy_event.get("writer_instance")) == original_writer,
            "database state changed/lost across redeploy",
        )

        observed_metrics = metrics(nf, project_id, service_id)
        cpu = metric_values(observed_metrics.get("cpu"))
        memory = metric_values(observed_metrics.get("memory"))

        _, service_body = nf.request("GET", f"/projects/{project_id}/services/{service_id}")
        service = data_of(service_body) or {}
        _, addon_body = nf.request("GET", f"/projects/{project_id}/addons/{addon_id}")
        addon = data_of(addon_body) or {}

        return {
            "status": "PASS",
            "source_sha": source_sha,
            "project_id": project_id,
            "project_created_by_probe": project_created,
            "addon_id": addon_id,
            "addon_created_by_probe": addon_created,
            "service_id": service_id,
            "service_public_url": url,
            "plan_observation": plan_observation,
            "service_status": (service.get("status") if isinstance(service, dict) else None),
            "addon_status": (addon.get("status") if isinstance(addon, dict) else None),
            "startup_seconds": startup_seconds,
            "health": {
                "database": True,
                "secret_injected": True,
                "provider_egress": True,
            },
            "instance_ids": {
                "initial": first_instance,
                "after_restart": restart_instance,
                "after_redeploy": redeploy_instance,
            },
            "instance_changed_on_restart": restart_instance != first_instance,
            "instance_changed_on_redeploy": redeploy_instance != restart_instance,
            "durable_marker": marker,
            "durable_state_survived_restart": True,
            "durable_state_survived_redeploy": True,
            "metrics": {
                "cpu_unit": (observed_metrics.get("cpu") or {}).get("metricInfo", {}).get("metricUnit")
                if isinstance(observed_metrics.get("cpu"), dict)
                else None,
                "cpu_samples": cpu,
                "cpu_max": max(cpu) if cpu else None,
                "memory_unit": (observed_metrics.get("memory") or {}).get("metricInfo", {}).get("metricUnit")
                if isinstance(observed_metrics.get("memory"), dict)
                else None,
                "memory_samples": memory,
                "memory_max": max(memory) if memory else None,
            },
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "keep_resources": keep_resources,
            "secret_value_recorded": False,
            "database_uri_recorded": False,
        }
    finally:
        if not keep_resources:
            if service_id:
                delete_resource(nf, f"/projects/{project_id}/services/{service_id}")
                cleanup["service_delete_requested"] = True
            if secret_id:
                delete_resource(nf, f"/projects/{project_id}/secrets/{secret_id}")
                cleanup["secret_delete_requested"] = True
            if addon_id:
                delete_resource(nf, f"/projects/{project_id}/addons/{addon_id}")
                cleanup["addon_delete_requested"] = True
        # The project is intentionally retained: a Sandbox may permit only one
        # project and P0-07/P0-08 depend on the same host experiment lineage.


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default=os.environ.get("NORTHFLANK_API_TOKEN"))
    parser.add_argument("--source-sha", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--region", default=os.environ.get("NORTHFLANK_REGION", "europe-west"))
    parser.add_argument("--project-id", default=os.environ.get("NORTHFLANK_PROJECT_ID"))
    parser.add_argument("--keep-resources", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("NORTHFLANK_API_TOKEN or --token is required")
    if not args.source_sha or len(args.source_sha) != 40:
        raise SystemExit("GITHUB_SHA/--source-sha must be an exact 40-character commit SHA")

    result = run_probe(
        args.token,
        args.source_sha,
        args.region,
        args.project_id,
        args.keep_resources,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True, default=str)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
