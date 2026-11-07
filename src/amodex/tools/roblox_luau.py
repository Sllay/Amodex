"""Supervised, single-submission Luau tool; no durable recovery or global secrets.

The runner supplies the resource, expected output and approval of an exact source
hash. Model arguments cannot choose destinations, credentials, or expectations.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable

import httpx

from amodex.connectors.models import ConnectorResourceRecord
from amodex.integrations.roblox_cloud import (
    EXECUTE_OPERATION, RobloxCloudError, RobloxCloudTarget, RobloxLuauClient,
)
from amodex.tools.scoped_workspace import ScopedWorkspaceTools, SecretDenied, ToolPolicyError

TOOL_NAME = "amodex_roblox_luau_test"
SECRET_NAME = "AMODEX_ROBLOX_OPEN_CLOUD_API_KEY"


@dataclass(frozen=True)
class SupervisedLuauApproval:
    """Trusted runner input, issued after review of pure source; never model input.

    Share one tool instance for the task. Its consumed flag is process-local,
    not an external idempotency guarantee. Do not reconstruct after a POST.
    authority_check must consult Run/lease authority when that context exists;
    a standalone supervisor may explicitly grant a short, one-shot session.
    """

    package_id: str
    task_id: str
    attempt_id: str
    environment_id: str
    resource_id: str
    target: RobloxCloudTarget
    source_sha256: str
    expected_results_json: str
    expected_logs: tuple[str, ...]
    authority_check: Callable[[], bool]


class RobloxLuauTestTool:
    def __init__(self, *, tools: ScopedWorkspaceTools, environment_id: str,
                 resource: ConnectorResourceRecord, approval: SupervisedLuauApproval,
                 analyzer: Path | None = None, http: httpx.Client | None = None) -> None:
        self.tools = tools
        self.environment_id = environment_id
        # Snapshot runner metadata so a later mutation cannot retarget this tool.
        self.resource = resource.model_copy(deep=True)
        self.approval = approval
        self.analyzer = analyzer
        if analyzer is not None:
            resolved = analyzer.resolve(strict=True)
            if (not analyzer.is_absolute() or not resolved.is_file() or not os.access(resolved, os.X_OK)
                    or resolved == tools.workspace or tools.workspace in resolved.parents):
                raise ValueError("Luau analyzer must be a trusted executable outside the workspace")
            self.analyzer = resolved
        self._expected = json.loads(approval.expected_results_json)
        if not isinstance(self._expected, list) or not self._expected or not approval.expected_logs:
            raise ValueError("supervisor must supply expected results and log evidence")
        if not re.fullmatch(r"[0-9a-f]{64}", approval.source_sha256):
            raise ValueError("supervisor must approve an exact source SHA256")
        # Capture official proxy/CA configuration before Hermes sanitizes its env.
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=10, follow_redirects=False)
        self._lock = Lock()
        self._revoked = Event()
        self._submitted = False

    @property
    def available(self) -> bool:
        return EXECUTE_OPERATION in self.tools.task.scope.allowed_operations

    def revoke(self) -> None:
        self._revoked.set()

    def close(self) -> None:
        self.revoke()
        if self._owns_http:
            self._http.close()

    def _authorize(self) -> None:
        task, approval = self.tools.task, self.approval
        now = datetime.now(timezone.utc)
        if (not self.available or self._revoked.is_set()
                or not task.issued_at <= now < task.expires_at
                or (str(task.package_id), str(task.task_id), str(task.attempt_id))
                != (approval.package_id, approval.task_id, approval.attempt_id)):
            raise ToolPolicyError("TASK_AUTHORITY_BLOCKED")
        target = RobloxCloudTarget.from_resource(
            self.resource, environment_id=self.environment_id, version_id=approval.target.version_id,
        )
        if (self.environment_id != approval.environment_id
                or str(self.resource.resource_id) != approval.resource_id or target != approval.target):
            raise ToolPolicyError("ROBLOX_TARGET_BLOCKED")
        if approval.authority_check() is not True:
            raise ToolPolicyError("RUN_AUTHORITY_BLOCKED")

    def _analyze(self, source: str) -> dict[str, Any]:
        if self.analyzer is None:
            return {"status": "UNAVAILABLE"}
        # Analyze the exact bytes subsequently submitted, without project config,
        # user-controlled argv, shell, inherited credentials, or executing Luau.
        with tempfile.TemporaryDirectory(prefix="amodex-luau-") as directory:
            snapshot = Path(directory) / "test.luau"
            snapshot.write_text(source, encoding="utf-8")
            try:
                result = subprocess.run(
                    [str(self.analyzer), str(snapshot)], cwd=directory,
                    env={"LANG": "C", "HOME": directory}, shell=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return {"status": "ERROR"}
        return {"status": "PASS" if result.returncode == 0 else "FAIL", "returncode": result.returncode}

    @staticmethod
    def _safe_logs(logs: tuple[str, ...], secret: str) -> tuple[list[str], bool]:
        # Redact exact injected material plus common credential assignments.
        safe = []
        truncated = len(logs) > 40
        for line in logs[:40]:
            line = line.replace(secret, "[REDACTED]")
            line = re.sub(r"(?i)bearer\s+\S+", "[REDACTED]", line)
            line = re.sub(r"(?i)(?:x-api-key|authorization|api[_-]?key|token|secret)\s*[:=].*",
                          "[REDACTED]", line)
            line = re.sub(r"https?://\S+", "[URL REDACTED]", line)
            line = "".join(c for c in line if c.isprintable())
            truncated |= len(line) > 200
            safe.append(line[:200])
        return safe, truncated

    def invoke(self, path: str, *, remote: bool = False) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "status": "BLOCKED", "code": "TOOL_BUSY"}
        api = None
        task = None
        local = None
        try:
            self._authorize()
            if type(remote) is not bool:
                raise ToolPolicyError("REMOTE_FLAG_MUST_BE_BOOLEAN")
            if not path.endswith(".luau"):
                raise ToolPolicyError("LUAU_FILE_REQUIRED")
            source = self.tools.read_file(path, max_bytes=64_000)
            sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
            local = self._analyze(source)
            base = {"path": path, "source_sha256": sha, "local_validation": local}
            if not remote:
                return {**base, "ok": local["status"] == "PASS", "status": "LOCAL_" + local["status"]}
            if self._submitted:
                return {**base, "ok": False, "status": "BLOCKED", "code": "SUBMISSION_ALREADY_USED", "resubmit": False}
            if sha != self.approval.source_sha256:
                raise ToolPolicyError("SOURCE_NOT_APPROVED")
            if local["status"] != "PASS":
                return {**base, "ok": False, "status": "LOCAL_BLOCKED", "resubmit": False}
            key = self.tools.read_secret(SECRET_NAME)
            api = RobloxLuauClient(target=self.approval.target, api_key=key, http=self._http,
                                   authorize_request=self._authorize)
            self._authorize()
            # Consume BEFORE submission. Neither HTTP failure nor ambiguity resets it.
            self._submitted = True
            task = api.submit(source)
            task = api.wait(task, deadline_seconds=30, max_polls=15)
            logs = api.collect_logs(task.path, max_pages=2)
            safe_logs, truncated = self._safe_logs(logs, key)
            results_match = json.dumps(list(task.results or ()), sort_keys=True) == json.dumps(self._expected, sort_keys=True)
            logs_match = all(line in safe_logs for line in self.approval.expected_logs)
            passed = task.state == "COMPLETE" and results_match and logs_match
            return {**base, "ok": passed, "status": "PASS" if passed else "TEST_FAILED",
                    "http_status": api.last_http_status, "state": task.state, "task_path": task.path,
                    "results_match": results_match, "logs_match": logs_match,
                    "verified_results": self._expected if results_match else None,
                    "logs": safe_logs, "logs_truncated": truncated, "resubmit": False}
        except RobloxCloudError as exc:
            return {"ok": False, "status": "UNKNOWN_OUTCOME" if exc.ambiguous_outcome else "BLOCKED",
                    "code": exc.code, "http_status": exc.http_status, "task_path": exc.task_path or (task.path if task else None),
                    "state": task.state if task else None, "local_validation": local, "resubmit": False}
        except SecretDenied:
            return {"ok": False, "status": "BLOCKED", "code": "SECRET_UNAVAILABLE", "resubmit": False}
        except Exception:
            # Never return source, raw diagnostics, arbitrary result strings or exception text.
            return {"ok": False, "status": "UNKNOWN_OUTCOME" if self._submitted else "BLOCKED",
                    "code": "POLICY_OR_INPUT_BLOCKED", "task_path": task.path if task else None, "resubmit": False}
        finally:
            self._lock.release()
