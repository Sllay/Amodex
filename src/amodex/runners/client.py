"""Authenticated runner-side Control Plane client.

The disposable runner receives only a job identifier plus one runner-control token.
It does not receive provider credentials or durable project authority.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class RunnerClientError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunnerControlClient:
    base_url: str
    token: str
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("Control Plane URL is required")
        if not self.token:
            raise ValueError("runner control token is required")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = httpx.request(
                method,
                self.base_url.rstrip("/") + path,
                headers={"authorization": f"Bearer {self.token}"},
                json=payload,
                params=params,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise RunnerClientError(f"Control Plane request failed: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:1000]}
        if response.status_code >= 400:
            detail = body.get("detail", body) if isinstance(body, dict) else body
            raise RunnerClientError(json.dumps(detail, ensure_ascii=False, sort_keys=True))
        if not isinstance(body, dict):
            raise RunnerClientError("Control Plane returned a non-object response")
        return body

    def claim(self, *, job_id: str, github_run_id: str | None = None) -> dict[str, Any]:
        params = {"github_run_id": github_run_id} if github_run_id else None
        return self._request("POST", f"/v1/runner-jobs/{job_id}/claim", params=params)

    def running(self, *, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/runner-jobs/{job_id}/running")

    def report(
        self,
        *,
        job_id: str,
        state: str,
        summary: str,
        result_commit: str | None = None,
        github_run_id: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/runner-jobs/{job_id}/report",
            payload={
                "state": state,
                "summary": summary,
                "result_commit": result_commit,
                "github_run_id": github_run_id,
                "evidence": evidence or {},
            },
        )


def _client_from_environment() -> RunnerControlClient:
    return RunnerControlClient(
        base_url=os.environ.get("AMODEX_CONTROL_PLANE_URL", ""),
        token=os.environ.get("AMODEX_RUNNER_CONTROL_TOKEN", ""),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m amodex.runners.client")
    sub = parser.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim")
    claim.add_argument("job_id")
    claim.add_argument("--github-run-id")
    claim.add_argument("--output")

    running = sub.add_parser("running")
    running.add_argument("job_id")

    report = sub.add_parser("report")
    report.add_argument("job_id")
    report.add_argument("--state", choices=("COMPLETED", "FAILED", "CANCELLED"), required=True)
    report.add_argument("--summary", required=True)
    report.add_argument("--result-commit")
    report.add_argument("--github-run-id")
    report.add_argument("--evidence-json")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    client = _client_from_environment()
    try:
        if args.command == "claim":
            result = client.claim(job_id=args.job_id, github_run_id=args.github_run_id)
            if args.output:
                Path(args.output).write_text(
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
        elif args.command == "running":
            result = client.running(job_id=args.job_id)
        else:
            evidence: dict[str, Any] = {}
            if args.evidence_json:
                evidence = json.loads(Path(args.evidence_json).read_text(encoding="utf-8"))
                if not isinstance(evidence, dict):
                    raise RunnerClientError("evidence JSON must be an object")
            result = client.report(
                job_id=args.job_id,
                state=args.state,
                summary=args.summary,
                result_commit=args.result_commit,
                github_run_id=args.github_run_id,
                evidence=evidence,
            )
    except (RunnerClientError, ValueError, json.JSONDecodeError) as exc:
        print(f"amodex-runner: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
