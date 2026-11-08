"""Authenticated runner-side Control Plane client.

The disposable runner receives only a job identifier plus one runner-control token.
It does not receive provider credentials or durable project authority.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import math
from pathlib import Path
from typing import Any

import httpx

from amodex.environments.product import require_github_run_id, RunnerReportRequest, RunnerTerminalReceipt
from amodex.contracts.ids import validate_uuid7


class AuthorityStatus(StrEnum):
    HEALTHY = "HEALTHY"
    LEASE_LOST = "LEASE_LOST"
    AUTH_REJECTED = "AUTH_REJECTED"
    AUTHORITY_UNCONFIRMED = "AUTHORITY_UNCONFIRMED"
    CONTROL_PLANE_ERROR = "CONTROL_PLANE_ERROR"


LEASE_CODES = frozenset({"RUNNER_LEASE_LOST", "RUNNER_LEASE_OWNER_MISMATCH", "RUNNER_LEASE_MISSING",
                         "RUNNER_JOB_STATE_CONFLICT", "RUNNER_JOB_NOT_CLAIMABLE", "RUNNER_JOB_TERMINAL_CONFLICT",
                         "RUNNER_ATTEMPT_MISMATCH"})


class RunnerClientError(RuntimeError):
    def __init__(self, status: AuthorityStatus = AuthorityStatus.AUTHORITY_UNCONFIRMED,
                 *, http_status: int | None = None, api_code: str | None = None):
        self.status, self.http_status, self.api_code = status, http_status, api_code
        super().__init__(status.value)


@dataclass(frozen=True)
class HeartbeatResult:
    status: AuthorityStatus
    http_status: int | None = None
    api_code: str | None = None
    lease_expires_at: datetime | None = None
    attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunnerControlClient:
    base_url: str = field(repr=False)
    token: str = field(repr=False)
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("Control Plane URL is required")
        if not self.token:
            raise ValueError("runner control token is required")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 30:
            raise ValueError("runner timeout must be in (0,30] seconds")

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
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise RunnerClientError() from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code != 200:
            detail = body.get("detail") if isinstance(body, dict) else None
            code = detail.get("code") if isinstance(detail, dict) else None
            code = code if isinstance(code, str) and code in LEASE_CODES | {"RUNNER_RECEIPT_NOT_FOUND", "RUNNER_REPORT_CONFLICT"} else None
            status = (AuthorityStatus.AUTH_REJECTED if response.status_code in {401, 403} else
                      AuthorityStatus.CONTROL_PLANE_ERROR if response.status_code >= 500 else
                      AuthorityStatus.LEASE_LOST if response.status_code == 404 and code != "RUNNER_RECEIPT_NOT_FOUND" or response.status_code == 409 and code in LEASE_CODES else
                      AuthorityStatus.AUTHORITY_UNCONFIRMED)
            raise RunnerClientError(status, http_status=response.status_code, api_code=code)
        if not isinstance(body, dict):
            raise RunnerClientError(http_status=response.status_code)
        return body

    def claim(self, *, job_id: str, github_run_id: str) -> dict[str, Any]:
        params = {"github_run_id": require_github_run_id(github_run_id)}
        return self._request("POST", f"/v1/runner-jobs/{job_id}/claim", params=params)

    def running(self, *, job_id: str, github_run_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/runner-jobs/{job_id}/running",
                             payload={"github_run_id": require_github_run_id(github_run_id)})

    def heartbeat(self, *, job_id: str, github_run_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/runner-jobs/{job_id}/heartbeat",
            payload={"github_run_id": require_github_run_id(github_run_id)},
        )

    def check_authority(self, *, job_id: str, github_run_id: str) -> HeartbeatResult:
        """Renew once; require a complete, current acknowledgement of this owner."""
        try:
            body = self.heartbeat(job_id=job_id, github_run_id=github_run_id)
            if body.get("job_id") != job_id or not body.get("github_run_id") or not body.get("state"):
                return HeartbeatResult(AuthorityStatus.AUTHORITY_UNCONFIRMED)
            if body["github_run_id"] != github_run_id or body["state"] not in {"CLAIMED", "RUNNING"}:
                return HeartbeatResult(AuthorityStatus.LEASE_LOST)
            expiry = datetime.fromisoformat(body["lease_expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                return HeartbeatResult(AuthorityStatus.AUTHORITY_UNCONFIRMED)
            if expiry <= datetime.now(timezone.utc):
                return HeartbeatResult(AuthorityStatus.LEASE_LOST, lease_expires_at=expiry)
            attempt = validate_uuid7(body["execution_attempt_id"])
            return HeartbeatResult(AuthorityStatus.HEALTHY, 200, lease_expires_at=expiry, attempt_id=attempt)
        except RunnerClientError as exc:
            return HeartbeatResult(exc.status, exc.http_status, exc.api_code)
        except (ValueError, KeyError, TypeError, AttributeError):
            return HeartbeatResult(AuthorityStatus.AUTHORITY_UNCONFIRMED)

    def report(
        self,
        *,
        job_id: str,
        state: str,
        summary: str,
        result_commit: str | None = None,
        github_run_id: str,
        attempt_id: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = RunnerReportRequest(state=state, summary=summary, result_commit=result_commit,
            github_run_id=github_run_id, attempt_id=attempt_id, evidence=evidence or {})
        body.safe_evidence()
        digest = body.digest(job_id)
        value = self._request("POST", f"/v1/runner-jobs/{job_id}/report", payload=body.model_dump(mode="json"))
        receipt = self._verified_receipt(value, job_id, github_run_id, body.attempt_id, digest)
        if receipt["state"] != body.state.value:
            raise RunnerClientError()
        return receipt

    def terminal_receipt(self, *, job_id: str, github_run_id: str, attempt_id: str,
                         report_digest: str) -> dict[str, Any]:
        value = self._request("GET", f"/v1/runner-jobs/{job_id}/terminal-receipt", params={
            "github_run_id": require_github_run_id(github_run_id), "attempt_id": validate_uuid7(attempt_id),
            "report_digest": report_digest})
        return self._verified_receipt(value, job_id, github_run_id, attempt_id, report_digest)

    @staticmethod
    def _verified_receipt(value, job_id, owner, attempt_id, digest) -> dict[str, Any]:
        try:
            receipt = RunnerTerminalReceipt.model_validate(value)
            if (receipt.job_id, receipt.github_run_id, receipt.attempt_id, receipt.report_digest) != (job_id, owner, attempt_id, digest):
                raise ValueError()
            return receipt.model_dump(mode="json")
        except ValueError:
            raise RunnerClientError() from None


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
    claim.add_argument("--github-run-id", required=True)
    claim.add_argument("--output")

    running = sub.add_parser("running")
    running.add_argument("job_id")
    running.add_argument("--github-run-id", required=True)

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("job_id")
    heartbeat.add_argument("--github-run-id", required=True)

    report = sub.add_parser("report")
    report.add_argument("job_id")
    report.add_argument("--state", choices=("COMPLETED", "FAILED", "CANCELLED"), required=True)
    report.add_argument("--summary", required=True)
    report.add_argument("--result-commit")
    report.add_argument("--github-run-id", required=True)
    report.add_argument("--evidence-json")
    report.add_argument("--attempt-id", required=True)
    receipt = sub.add_parser("receipt")
    receipt.add_argument("job_id")
    receipt.add_argument("--github-run-id", required=True)
    receipt.add_argument("--attempt-id", required=True)
    receipt.add_argument("--report-digest", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    client = _client_from_environment()
    try:
        if args.command == "claim":
            result = client.claim(job_id=args.job_id, github_run_id=args.github_run_id)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as out:
                    os.fchmod(out.fileno(), 0o600)
                    json.dump(result, out, ensure_ascii=False, sort_keys=True)
        elif args.command == "running":
            result = client.running(job_id=args.job_id, github_run_id=args.github_run_id)
        elif args.command == "heartbeat":
            result = client.heartbeat(job_id=args.job_id, github_run_id=args.github_run_id)
        elif args.command == "receipt":
            result = client.terminal_receipt(job_id=args.job_id, github_run_id=args.github_run_id,
                attempt_id=args.attempt_id, report_digest=args.report_digest)
        else:
            evidence: dict[str, Any] = {}
            if args.evidence_json:
                evidence = json.loads(Path(args.evidence_json).read_text(encoding="utf-8"))
                if not isinstance(evidence, dict):
                    raise RunnerClientError()
            result = client.report(
                job_id=args.job_id,
                state=args.state,
                summary=args.summary,
                result_commit=args.result_commit,
                github_run_id=args.github_run_id,
                evidence=evidence,
                attempt_id=args.attempt_id,
            )
    except (RunnerClientError, ValueError, OSError):
        print('amodex-runner: CONTROL_REQUEST_BLOCKED', file=sys.stderr)
        raise SystemExit(2) from None
    # Full claim context belongs in the private output file, never CI logs.
    confirmation = {"job_id": args.job_id, "status": "ACKNOWLEDGED"}
    if args.command in {"report", "receipt"}:
        confirmation.update({key: result[key] for key in ("receipt_id", "attempt_id", "report_digest", "state")})
    print(json.dumps(confirmation, sort_keys=True))


if __name__ == "__main__":
    main()
