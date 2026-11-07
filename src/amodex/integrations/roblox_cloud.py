"""Small Open Cloud Luau boundary. No publishing, cookies, dispatch or write retries."""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from pydantic import SecretStr

from amodex.connectors.models import ConnectorResourceRecord, ConnectorResourceType
from amodex.integrations.models import IntegrationCapability, ToolBinding
from amodex.tools.scoped_workspace import ScopedWorkspaceTools, ScopeViolation

BINDING = ToolBinding(
    name="roblox.open_cloud.luau", transport="https",
    capabilities=(IntegrationCapability.RUNTIME_EXECUTE, IntegrationCapability.CONSOLE,
                  IntegrationCapability.DIAGNOSTICS),
)
EXECUTE_OPERATION = "roblox.luau.execute"
_TERMINAL = {"COMPLETE", "FAILED", "CANCELLED"}
_STATES = {"QUEUED", "PROCESSING", *_TERMINAL}
_TASK_PATH = re.compile(
    r"universes/([1-9][0-9]*)/places/([1-9][0-9]*)/versions/([1-9][0-9]*)/"
    r"luau-execution-sessions/[A-Za-z0-9_-]{1,100}/tasks/[A-Za-z0-9_-]{1,100}"
)


class RobloxCloudError(RuntimeError):
    def __init__(self, code: str, *, ambiguous_outcome: bool = False,
                 task_path: str | None = None, http_status: int | None = None):
        super().__init__(code)
        self.code = code
        self.ambiguous_outcome = ambiguous_outcome
        self.task_path = task_path
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class RobloxCloudTarget:
    universe_id: str
    place_id: str
    version_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.universe_id, self.place_id, self.version_id):
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value)):
                raise ValueError("Roblox IDs must be positive decimal strings")
        if self.universe_id is None or self.place_id is None:
            raise ValueError("universe and place are required")

    @classmethod
    def from_resource(cls, resource: ConnectorResourceRecord, *, environment_id: str,
                      version_id: str | None = None) -> RobloxCloudTarget:
        if (resource.resource_type != ConnectorResourceType.ROBLOX_PLACE
                or not environment_id or resource.bound_environment_id != environment_id):
            raise ScopeViolation("select a Roblox place bound to this environment")
        return cls(universe_id=resource.metadata.get("universe_id"),
                   place_id=resource.external_id, version_id=version_id)


@dataclass(frozen=True, slots=True)
class LuauTask:
    path: str
    state: str
    results: tuple[Any, ...] | None = None
    error_code: str | None = None


class RobloxLuauClient:
    BASE = "https://apis.roblox.com/cloud/v2/"

    def __init__(self, *, target: RobloxCloudTarget, api_key: str,
                 http: httpx.Client | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        if not isinstance(api_key, str) or not api_key or not api_key.isascii() or any(c.isspace() or ord(c) < 33 for c in api_key):
            raise ValueError("Open Cloud API key is missing or invalid")
        self.target = target
        self._key = SecretStr(api_key)
        self._owns_http = http is None
        self._http = http or httpx.Client(timeout=10, follow_redirects=False)
        self._clock, self._sleep = clock, sleep

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> RobloxLuauClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _path(self, path: str) -> str:
        match = _TASK_PATH.fullmatch(path) if isinstance(path, str) else None
        if (match is None or (match[1], match[2]) != (self.target.universe_id, self.target.place_id)
                or self.target.version_id is not None and match[3] != self.target.version_id):
            raise RobloxCloudError("UNTRUSTED_TASK_PATH")
        return path

    def _request(self, method: str, path: str, *, task_path: str | None = None,
                 timeout: float = 10, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._http.request(
                method, self.BASE + path, headers={"x-api-key": self._key.get_secret_value()},
                timeout=timeout, follow_redirects=False, **kwargs,
            )
        except httpx.RequestError as exc:
            code = "HTTP_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "HTTP_TRANSPORT_ERROR"
            raise RobloxCloudError(code, ambiguous_outcome=True, task_path=task_path) from None
        if not response.is_success:
            code = {401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 429: "RATE_LIMITED"}.get(response.status_code, "HTTP_ERROR")
            ambiguous = method == "GET" or response.status_code not in {400, 401, 403, 404, 409, 422, 429}
            raise RobloxCloudError(code, ambiguous_outcome=ambiguous, task_path=task_path,
                                   http_status=response.status_code)
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except ValueError:
            raise RobloxCloudError("INVALID_RESPONSE", ambiguous_outcome=True, task_path=task_path) from None

    def _task(self, body: dict[str, Any], *, expected_path: str | None = None) -> LuauTask:
        known_path = expected_path
        try:
            path = self._path(body.get("path"))
            known_path = path
            state = body.get("state")
            if state not in _STATES or expected_path is not None and path != expected_path:
                raise ValueError()
            results = body.get("output", {}).get("results")
            if results is not None and not isinstance(results, list):
                raise ValueError()
            if state == "COMPLETE" and (not isinstance(results, list) or "error" in body):
                raise ValueError()
            error_code = body.get("error", {}).get("code")
            if error_code not in {None, "SCRIPT_ERROR", "DEADLINE_EXCEEDED", "OUTPUT_SIZE_LIMIT_EXCEEDED", "INTERNAL_ERROR", "ERROR_CODE_UNSPECIFIED"}:
                raise ValueError()
            return LuauTask(path, state, tuple(results) if results is not None else None, error_code)
        except (RobloxCloudError, ValueError, TypeError, AttributeError):
            # A successful POST with an unusable receipt may already have executed.
            raise RobloxCloudError("INVALID_TASK_RECEIPT", ambiguous_outcome=True,
                                   task_path=known_path) from None

    def submit(self, script: str, *, script_timeout_seconds: int = 10) -> LuauTask:
        if not isinstance(script, str) or not script.strip() or len(script.encode("utf-8")) > 64_000:
            raise ValueError("script must contain 1..64000 UTF-8 bytes")
        if type(script_timeout_seconds) is not int or not 1 <= script_timeout_seconds <= 300:
            raise ValueError("script timeout must be an integer in 1..300 seconds")
        path = f"universes/{self.target.universe_id}/places/{self.target.place_id}/"
        if self.target.version_id is not None:
            path += f"versions/{self.target.version_id}/"
        body = self._request("POST", path + "luau-execution-session-tasks",
                             json={"script": script, "timeout": f"{script_timeout_seconds}s"})
        return self._task(body)

    def submit_scoped(self, tools: ScopedWorkspaceTools, path: str) -> LuauTask:
        if EXECUTE_OPERATION not in tools.task.scope.allowed_operations:
            raise ScopeViolation("TaskPackage does not grant roblox.luau.execute")
        return self.submit(tools.read_file(path, max_bytes=64_000))

    def get_task(self, path: str, *, timeout: float = 10) -> LuauTask:
        path = self._path(path)
        return self._task(self._request("GET", path, task_path=path, timeout=timeout,
                                        params={"view": "BASIC"}), expected_path=path)

    def wait(self, task: LuauTask, *, deadline_seconds: float = 30,
             poll_interval_seconds: float = 2, max_polls: int = 20) -> LuauTask:
        self._path(task.path)
        if (type(deadline_seconds) not in {int, float} or not math.isfinite(deadline_seconds)
                or not 0 < deadline_seconds <= 300
                or type(poll_interval_seconds) not in {int, float} or not math.isfinite(poll_interval_seconds)
                or not 0.1 <= poll_interval_seconds <= 10
                or type(max_polls) is not int or not 1 <= max_polls <= 60):
            raise ValueError("polling limits are invalid")
        end = self._clock() + deadline_seconds
        for _ in range(max_polls):
            if task.state in _TERMINAL:
                return task
            remaining = end - self._clock()
            if remaining <= 0:
                break
            self._sleep(min(poll_interval_seconds, remaining))
            remaining = end - self._clock()
            if remaining <= 0:
                break
            task = self.get_task(task.path, timeout=min(10, remaining))
        if task.state in _TERMINAL:
            return task
        raise RobloxCloudError("POLL_DEADLINE", ambiguous_outcome=True, task_path=task.path)

    def collect_logs(self, path: str, *, max_pages: int = 5) -> tuple[str, ...]:
        path = self._path(path)
        if type(max_pages) is not int or not 1 <= max_pages <= 10:
            raise ValueError("max_pages must be in 1..10")
        messages: list[str] = []
        token, seen = "", set()
        for _ in range(max_pages):
            body = self._request("GET", path + "/logs", task_path=path,
                                 params={"view": "FLAT", "maxPageSize": 100, "pageToken": token})
            chunks = body.get("luauExecutionSessionTaskLogs", [])
            if not isinstance(chunks, list):
                raise RobloxCloudError("INVALID_LOGS", task_path=path)
            for chunk in chunks:
                lines = chunk.get("messages") if isinstance(chunk, dict) else None
                if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
                    raise RobloxCloudError("INVALID_LOGS", task_path=path)
                messages.extend(lines)
            token = body.get("nextPageToken", "")
            if not token:
                return tuple(messages)
            if not isinstance(token, str) or token in seen:
                break
            seen.add(token)
        raise RobloxCloudError("LOGS_INCOMPLETE", task_path=path)
