"""Termux-friendly Amodex CLI.

The CLI is deliberately stateless with respect to run authority. Every command
reads/writes through the Control Plane API so closing the terminal cannot cancel
or orphan a remote run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx


TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "UNKNOWN_OUTCOME", "BLOCKED"}


class CliError(RuntimeError):
    pass


@dataclass(slots=True)
class AmodexClient:
    base_url: str
    timeout_seconds: float = 30.0

    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> Any:
        url = self.base_url.rstrip("/") + path
        try:
            response = httpx.request(method, url, json=payload, timeout=self.timeout_seconds)
        except httpx.HTTPError as exc:
            raise CliError(f"cannot reach Amodex Control Plane: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:1000]}
        if response.status_code >= 400:
            detail = body.get("detail", body) if isinstance(body, dict) else body
            if isinstance(detail, dict):
                message = detail.get("message") or detail.get("code") or json.dumps(detail, ensure_ascii=False)
                recovery = detail.get("recovery")
                job_id = detail.get("job_id")
                suffix = f"\nJob: {job_id}" if job_id else ""
                raise CliError(
                    f"{message}"
                    + (f"\nRecovery: {recovery}" if recovery else "")
                    + suffix
                )
            raise CliError(str(detail))
        return body


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amodex", description="Amodex remote control CLI")
    parser.add_argument("--url", default=os.environ.get("AMODEX_URL", "http://127.0.0.1:8000"))
    sub = parser.add_subparsers(dest="command", required=True)

    project = sub.add_parser("project", help="project operations")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_add = project_sub.add_parser("add")
    project_add.add_argument("repo")
    project_add.add_argument("--name")
    project_sub.add_parser("list")

    env = sub.add_parser("env", help="isolated Environment operations")
    env_sub = env.add_subparsers(dest="env_command", required=True)
    env_create = env_sub.add_parser("create")
    env_create.add_argument("name")
    env_create.add_argument("--engine", help="godot, roblox, or another engine label")
    env_sub.add_parser("list")
    env_show = env_sub.add_parser("show")
    env_show.add_argument("environment_id")
    env_use = env_sub.add_parser("use", help="validate an Environment and print shell export guidance")
    env_use.add_argument("environment_id")
    env_bind = env_sub.add_parser("bind-project")
    env_bind.add_argument("environment_id")
    env_bind.add_argument("project_id")

    session = env_sub.add_parser("session", help="Environment session operations")
    session_sub = session.add_subparsers(dest="env_session_command", required=True)
    session_create = session_sub.add_parser("create")
    session_create.add_argument("environment_id")
    session_create.add_argument("title")
    session_create.add_argument("--domain")
    session_list = session_sub.add_parser("list")
    session_list.add_argument("environment_id")

    env_usage = env_sub.add_parser("usage")
    env_usage.add_argument("environment_id")
    env_jobs = env_sub.add_parser("jobs")
    env_jobs.add_argument("environment_id")
    env_dispatch = env_sub.add_parser("dispatch", help="queue the correct runner for an Environment")
    env_dispatch.add_argument("environment_id")
    env_dispatch.add_argument("run_id")
    env_dispatch.add_argument("session_id")
    env_dispatch.add_argument("--profile", default="implementation")
    env_dispatch.add_argument(
        "--task-class",
        choices=("MICRO", "ROUTINE", "STANDARD", "COMPLEX", "CRITICAL"),
        default="STANDARD",
    )
    env_dispatch.add_argument("--write", action="append", default=[], metavar="PATH")

    run = sub.add_parser("run", help="start a durable remote run")
    run.add_argument("objective")
    run.add_argument("--project")
    run.add_argument("--environment", default=os.environ.get("AMODEX_ENVIRONMENT_ID"))
    run.add_argument("--session", default=os.environ.get("AMODEX_SESSION_ID"))

    status = sub.add_parser("status", help="show durable run state")
    status.add_argument("run_id")

    attach = sub.add_parser("attach", help="follow durable events; safe to disconnect")
    attach.add_argument("run_id")
    attach.add_argument("--interval", type=float, default=2.0)
    attach.add_argument("--once", action="store_true")

    for action in ("pause", "resume", "cancel"):
        p = sub.add_parser(action)
        p.add_argument("run_id")
        p.add_argument("--reason", default=f"owner requested {action} from CLI")

    approve = sub.add_parser("approve")
    approve.add_argument("approval_id")
    reject = sub.add_parser("reject")
    reject.add_argument("approval_id")

    promote = sub.add_parser("promote")
    promote.add_argument("candidate_id")

    provider = sub.add_parser("provider", help="provider connection references")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)
    provider_connect = provider_sub.add_parser("connect")
    provider_connect.add_argument("provider")
    provider_connect.add_argument("secret_ref", help="env://VARIABLE_NAME; raw keys are rejected")
    provider_connect.add_argument("--pool", required=True)
    provider_connect.add_argument("--billing", choices=("free", "paid", "unknown"), default="unknown")
    provider_sub.add_parser("list")

    sub.add_parser("system", help="show control-plane/zero-cost health")
    sub.add_parser("dashboard", help="print the same aggregate state used by the web UI")
    return parser


def execute(args: argparse.Namespace, client: AmodexClient) -> Any:
    if args.command == "project":
        if args.project_command == "add":
            return client.request("POST", "/v1/projects", payload={"repo_url": args.repo, "name": args.name})
        return client.request("GET", "/v1/projects")

    if args.command == "env":
        if args.env_command == "create":
            return client.request(
                "POST",
                "/v1/environments",
                payload={"name": args.name, "engine": args.engine},
            )
        if args.env_command == "list":
            return client.request("GET", "/v1/environments")
        if args.env_command == "show":
            return client.request("GET", f"/v1/environments/{args.environment_id}")
        if args.env_command == "use":
            item = client.request("GET", f"/v1/environments/{args.environment_id}")
            return {
                "environment": item,
                "shell": f"export AMODEX_ENVIRONMENT_ID={args.environment_id}",
                "note": "CLI remains stateless; export this variable in the current shell if desired.",
            }
        if args.env_command == "bind-project":
            return client.request(
                "POST",
                f"/v1/environments/{args.environment_id}/projects",
                payload={"project_id": args.project_id},
            )
        if args.env_command == "session":
            if args.env_session_command == "create":
                return client.request(
                    "POST",
                    f"/v1/environments/{args.environment_id}/sessions",
                    payload={"title": args.title, "domain_key": args.domain},
                )
            return client.request(
                "GET",
                f"/v1/environments/{args.environment_id}/sessions",
            )
        if args.env_command == "usage":
            return client.request("GET", f"/v1/environments/{args.environment_id}/usage")
        if args.env_command == "jobs":
            return client.request("GET", f"/v1/environments/{args.environment_id}/runner-jobs")
        if args.env_command == "dispatch":
            return client.request(
                "POST",
                f"/v1/environments/{args.environment_id}/runner-jobs",
                payload={
                    "run_id": args.run_id,
                    "session_id": args.session_id,
                    "worker_profile": args.profile,
                    "task_class": args.task_class,
                    "write_paths": args.write,
                },
            )
        raise CliError(f"unsupported env command: {args.env_command}")

    if args.command == "run":
        if bool(args.environment) != bool(args.session):
            raise CliError("--environment and --session must be supplied together")
        run = client.request(
            "POST",
            "/v1/runs",
            payload={"prompt": args.objective, "project_id": args.project},
        )
        if args.environment and args.session:
            if not args.project:
                raise CliError("Environment runs require --project")
            binding = client.request(
                "POST",
                f"/v1/environments/{args.environment}/sessions/{args.session}/runs/{run['run_id']}",
            )
            return {"run": run, "environment_binding": binding}
        return run
    if args.command == "status":
        return client.request("GET", f"/v1/runs/{args.run_id}")
    if args.command == "attach":
        return attach_run(client, args.run_id, interval=args.interval, once=args.once)
    if args.command in {"pause", "resume", "cancel"}:
        return client.request(
            "POST",
            f"/v1/runs/{args.run_id}/{args.command}",
            payload={"reason": args.reason},
        )
    if args.command in {"approve", "reject"}:
        return client.request("POST", f"/v1/approvals/{args.approval_id}/{args.command}")
    if args.command == "promote":
        return client.request("POST", f"/v1/candidates/{args.candidate_id}/promote")
    if args.command == "provider":
        if args.provider_command == "connect":
            return client.request(
                "POST",
                "/v1/provider-connections",
                payload={
                    "provider": args.provider,
                    "credential_pool_id": args.pool,
                    "secret_ref": args.secret_ref,
                    "billing_class": args.billing,
                    "enabled": True,
                },
            )
        return client.request("GET", "/v1/provider-connections")
    if args.command == "system":
        return client.request("GET", "/v1/system/status")
    if args.command == "dashboard":
        return client.request("GET", "/v1/dashboard")
    raise CliError(f"unsupported command: {args.command}")


def attach_run(client: AmodexClient, run_id: str, *, interval: float = 2.0, once: bool = False) -> dict[str, Any]:
    seen: set[str] = set()
    while True:
        events = client.request("GET", f"/v1/runs/{run_id}/events")
        for event in events:
            event_id = str(event.get("event_id"))
            if event_id in seen:
                continue
            seen.add(event_id)
            print(
                f"[{event.get('seq')}] {event.get('kind')} "
                f"{json.dumps(event.get('payload', {}), ensure_ascii=False, sort_keys=True)}"
            )
        run = client.request("GET", f"/v1/runs/{run_id}")
        if once or run.get("state") in TERMINAL_STATES:
            return run
        time.sleep(max(interval, 0.1))


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    client = AmodexClient(args.url)
    try:
        result = execute(args, client)
    except CliError as exc:
        print(f"amodex: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
