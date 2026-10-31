"""Stage 2 scoped workspace tools.

This is the deterministic tool/policy boundary for one disposable coding
runner. Global scheduling, promotion, and cross-task ownership remain separate
authorities.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Iterable, Mapping, Sequence
from uuid import uuid4

from amodex.contracts.models import TaskPackage


class ToolPolicyError(RuntimeError):
    pass


class PathPolicyError(ToolPolicyError):
    pass


class ScopeViolation(ToolPolicyError):
    pass


class PathLeaseConflict(ToolPolicyError):
    pass


class CommandDenied(ToolPolicyError):
    pass


class SecretDenied(ToolPolicyError):
    pass


@dataclass(frozen=True, slots=True)
class PathLease:
    lease_id: str
    owner: str
    path: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ContextFile:
    path: str
    sha256: str
    content: str


@dataclass(frozen=True, slots=True)
class ScopedContextCapsule:
    task_id: str
    expected_base_sha: str
    objective: str
    files: tuple[ContextFile, ...]


class PathLeaseRegistry:
    """Single-runner writer leases; Stage 3 later centralizes ownership."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._by_path: dict[str, PathLease] = {}
        self._by_id: dict[str, PathLease] = {}

    def acquire(self, *, owner: str, path: str) -> PathLease:
        with self._guard:
            if path in self._by_path:
                raise PathLeaseConflict(f"path already has active writer lease: {path!r}")
            lease = PathLease(str(uuid4()), owner, path)
            self._by_path[path] = lease
            self._by_id[lease.lease_id] = lease
            return lease

    def require(self, *, lease_id: str, owner: str, path: str) -> PathLease:
        with self._guard:
            lease = self._by_id.get(lease_id)
            if lease is None:
                raise PathLeaseConflict("writer lease is missing or released")
            if lease.owner != owner or lease.path != path:
                raise PathLeaseConflict("writer lease does not authorize this path")
            if self._by_path.get(path) != lease:
                raise PathLeaseConflict("writer lease is no longer active")
            return lease

    def release(self, *, lease_id: str, owner: str) -> None:
        with self._guard:
            lease = self._by_id.get(lease_id)
            if lease is None:
                raise PathLeaseConflict("writer lease is missing or already released")
            if lease.owner != owner:
                raise PathLeaseConflict("only the lease owner may release it")
            self._by_id.pop(lease_id, None)
            self._by_path.pop(lease.path, None)


class SecretBroker:
    """Task-scoped broker that never falls back to host environment secrets."""

    def __init__(self, *, values: Mapping[str, str] | None = None, granted_names: Iterable[str] = ()) -> None:
        self._values = dict(values or {})
        self._granted = frozenset(granted_names)

    def read(self, name: str) -> str:
        if name not in self._granted:
            raise SecretDenied(f"secret is not granted to this task: {name}")
        if name not in self._values:
            raise SecretDenied(f"granted secret has no bound value: {name}")
        return self._values[name]


class ScopedWorkspaceTools:
    """Trusted read/search/patch/test tools guarded by TaskPackage scope."""

    _MAX_FILE_BYTES = 1_000_000
    _MAX_OUTPUT_BYTES = 64_000
    _SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "HOME", "TMPDIR")

    def __init__(
        self,
        *,
        task: TaskPackage,
        workspace: Path,
        leases: PathLeaseRegistry | None = None,
        secrets: SecretBroker | None = None,
        allowed_validator_programs: Iterable[str] = ("python", "python3", "python3.13", "pytest"),
    ) -> None:
        self.task = task
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise PathPolicyError("workspace must be a directory")
        self.leases = leases or PathLeaseRegistry()
        self.secrets = secrets or SecretBroker()
        self.allowed_validator_programs = frozenset(allowed_validator_programs)

    @staticmethod
    def _normalize_relative(path: str) -> str:
        if not isinstance(path, str) or not path:
            raise PathPolicyError("path must be a non-empty workspace-relative string")
        if "\x00" in path or "\\" in path:
            raise PathPolicyError("invalid path encoding")
        pure = PurePosixPath(path)
        if pure.is_absolute() or any(part == ".." for part in pure.parts):
            raise PathPolicyError("absolute paths and traversal are forbidden")
        normalized = pure.as_posix()
        if normalized in {"", "."}:
            raise PathPolicyError("workspace root is not a file target")
        return normalized

    @staticmethod
    def _matches_scope(path: str, specs: Iterable[str]) -> bool:
        for raw in specs:
            spec = raw.strip("/")
            if not spec:
                continue
            if spec.endswith("/**"):
                prefix = spec[:-3].rstrip("/")
                if path == prefix or path.startswith(prefix + "/"):
                    return True
            elif path == spec:
                return True
        return False

    def _resolve(self, path: str) -> tuple[str, Path]:
        relative = self._normalize_relative(path)
        try:
            resolved = (self.workspace / relative).resolve(strict=False)
        except OSError as exc:
            raise PathPolicyError(f"path could not be resolved: {relative}") from exc
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise PathPolicyError(f"path resolves outside workspace: {relative}")
        return relative, resolved

    def _require_operation(self, operation: str) -> None:
        if operation not in self.task.scope.allowed_operations:
            raise ScopeViolation(f"operation not granted by TaskPackage: {operation}")

    def _authorize_read(self, relative: str) -> None:
        if not (self._matches_scope(relative, self.task.scope.read) or self._matches_scope(relative, self.task.scope.write)):
            raise ScopeViolation(f"read outside authorized scope: {relative}")

    def _authorize_write(self, relative: str) -> None:
        if self._matches_scope(relative, self.task.scope.protected):
            raise ScopeViolation(f"protected path cannot be written: {relative}")
        if not self._matches_scope(relative, self.task.scope.write):
            raise ScopeViolation(f"write outside authorized scope: {relative}")

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        self._require_operation("read_file")
        relative, resolved = self._resolve(path)
        self._authorize_read(relative)
        if not resolved.is_file():
            raise FileNotFoundError(relative)
        limit = max_bytes or self._MAX_FILE_BYTES
        size = resolved.stat().st_size
        if size > limit:
            raise ToolPolicyError(f"file exceeds read limit ({size} > {limit} bytes)")
        return resolved.read_text(encoding="utf-8")

    def search_files(self, pattern: str, *, max_results: int = 100) -> tuple[tuple[str, int, str], ...]:
        self._require_operation("search_files")
        if not 1 <= max_results <= 1000:
            raise ToolPolicyError("max_results must be between 1 and 1000")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolPolicyError(f"invalid search regex: {exc}") from exc
        results: list[tuple[str, int, str]] = []
        for candidate in sorted(self.workspace.rglob("*")):
            if len(results) >= max_results:
                break
            if ".git" in candidate.parts or not candidate.is_file():
                continue
            relative = candidate.relative_to(self.workspace).as_posix()
            if not (self._matches_scope(relative, self.task.scope.read) or self._matches_scope(relative, self.task.scope.write)):
                continue
            try:
                _, resolved = self._resolve(relative)
                if resolved.stat().st_size > self._MAX_FILE_BYTES:
                    continue
                text = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, PathPolicyError):
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append((relative, line_no, line))
                    if len(results) >= max_results:
                        break
        return tuple(results)

    def acquire_write_lease(self, path: str, *, owner: str) -> PathLease:
        self._require_operation("patch_file")
        relative, _ = self._resolve(path)
        self._authorize_write(relative)
        return self.leases.acquire(owner=owner, path=relative)

    def release_write_lease(self, *, lease_id: str, owner: str) -> None:
        self.leases.release(lease_id=lease_id, owner=owner)

    def patch_file(
        self,
        path: str,
        *,
        owner: str,
        lease_id: str,
        expected_sha256: str,
        old_text: str,
        new_text: str,
    ) -> str:
        self._require_operation("patch_file")
        relative, resolved = self._resolve(path)
        self._authorize_write(relative)
        self.leases.require(lease_id=lease_id, owner=owner, path=relative)
        if not resolved.is_file():
            raise FileNotFoundError(relative)
        original = resolved.read_bytes()
        observed_sha = hashlib.sha256(original).hexdigest()
        if observed_sha != expected_sha256:
            raise ToolPolicyError(f"stale patch precondition for {relative}")
        text = original.decode("utf-8")
        count = text.count(old_text)
        if count != 1:
            raise ToolPolicyError(f"patch anchor must occur exactly once; observed {count}")
        updated = text.replace(old_text, new_text, 1)
        temp_path = resolved.with_name(f".{resolved.name}.amodex-tmp-{uuid4().hex}")
        try:
            temp_path.write_text(updated, encoding="utf-8")
            os.replace(temp_path, resolved)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return hashlib.sha256(updated.encode("utf-8")).hexdigest()

    def run_validator(self, argv: Sequence[str], *, cwd: str = ".", timeout_seconds: int = 60) -> CommandResult:
        self._require_operation("run_tests")
        if not argv:
            raise CommandDenied("validator argv cannot be empty")
        program = Path(argv[0]).name
        if program not in self.allowed_validator_programs:
            raise CommandDenied(f"validator program is not allowed: {program}")
        if not 1 <= timeout_seconds <= 300:
            raise CommandDenied("validator timeout must be between 1 and 300 seconds")
        if cwd == ".":
            resolved_cwd = self.workspace
        else:
            relative_cwd, resolved_cwd = self._resolve(cwd)
            self._authorize_read(relative_cwd)
            if not resolved_cwd.is_dir():
                raise CommandDenied(f"validator cwd is not a directory: {cwd}")
        safe_env = {key: value for key, value in os.environ.items() if key in self._SAFE_ENV_KEYS}
        safe_env["PYTHONNOUSERSITE"] = "1"
        completed = subprocess.run(
            list(argv),
            cwd=resolved_cwd,
            env=safe_env,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return CommandResult(
            completed.returncode,
            completed.stdout[-self._MAX_OUTPUT_BYTES :],
            completed.stderr[-self._MAX_OUTPUT_BYTES :],
        )

    def read_secret(self, name: str) -> str:
        return self.secrets.read(name)

    def build_context_capsule(self, paths: Sequence[str]) -> ScopedContextCapsule:
        files: list[ContextFile] = []
        seen: set[str] = set()
        for requested in paths:
            relative, _ = self._resolve(requested)
            if relative in seen:
                continue
            seen.add(relative)
            content = self.read_file(relative)
            files.append(ContextFile(relative, hashlib.sha256(content.encode("utf-8")).hexdigest(), content))
        return ScopedContextCapsule(
            str(self.task.task_id),
            self.task.repository.expected_base_sha,
            self.task.objective.statement,
            tuple(files),
        )
