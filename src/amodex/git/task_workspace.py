"""Deterministic Git task workspace and checkpoint lifecycle for Stage 2."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import (
    Checkpoint,
    CheckpointClass,
    EvidenceResult,
    TaskPackage,
    ValidationEvidence,
)


class GitWorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerReport:
    task_id: str
    attempt_id: str
    status: str
    changed_files: tuple[str, ...]
    validation_evidence: ValidationEvidence
    safe_checkpoint: Checkpoint
    validated_checkpoint: Checkpoint


@dataclass(slots=True)
class GitTaskWorkspace:
    """Isolated clone bound to one TaskPackage.

    The service exposes no generic push-to-main primitive. It can push only the
    task branch and namespaced checkpoint refs.
    """

    task: TaskPackage
    repository_url: str
    path: Path

    @classmethod
    def create(cls, *, task: TaskPackage, repository_url: str, path: Path) -> "GitTaskWorkspace":
        if path.exists():
            raise GitWorkspaceError(f"workspace already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        cls._run_external(("git", "clone", "--no-checkout", repository_url, str(path)))
        workspace = cls(task=task, repository_url=repository_url, path=path)
        workspace._git("checkout", "--detach", task.repository.expected_base_sha)
        observed = workspace.current_sha()
        if observed != task.repository.expected_base_sha:
            raise GitWorkspaceError(
                f"clone base mismatch: expected {task.repository.expected_base_sha}, observed {observed}"
            )
        workspace._git("switch", "-c", task.repository.task_branch)
        workspace._git("config", "user.name", "Amodex Worker")
        workspace._git("config", "user.email", "worker@amodex.invalid")
        return workspace

    @classmethod
    def reconstruct(
        cls,
        *,
        task: TaskPackage,
        repository_url: str,
        checkpoint_sha: str,
        path: Path,
    ) -> "GitTaskWorkspace":
        if path.exists():
            raise GitWorkspaceError(f"workspace already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        cls._run_external(("git", "clone", "--no-checkout", repository_url, str(path)))
        workspace = cls(task=task, repository_url=repository_url, path=path)
        workspace._git("checkout", "--detach", checkpoint_sha)
        if workspace.current_sha() != checkpoint_sha:
            raise GitWorkspaceError("reconstructed checkpoint SHA mismatch")
        if workspace.status_porcelain():
            raise GitWorkspaceError("reconstructed checkpoint is not clean")
        return workspace

    @staticmethod
    def _run_external(argv: Sequence[str], *, cwd: Path | None = None) -> str:
        completed = subprocess.run(
            list(argv), cwd=cwd, capture_output=True, text=True, check=False
        )
        if completed.returncode != 0:
            raise GitWorkspaceError(
                f"command failed ({completed.returncode}): {' '.join(argv)}\n"
                f"{completed.stderr[-4000:]}"
            )
        return completed.stdout.strip()

    def _git(self, *args: str) -> str:
        return self._run_external(("git", *args), cwd=self.path)

    def current_sha(self) -> str:
        return self._git("rev-parse", "HEAD")

    def status_porcelain(self) -> tuple[str, ...]:
        raw = self._git("status", "--porcelain=v1", "--untracked-files=all")
        return tuple(line for line in raw.splitlines() if line) if raw else ()

    def changed_paths(self) -> tuple[str, ...]:
        raw = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        if not raw:
            return ()
        entries = raw.split("\x00")
        paths: list[str] = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            if not entry:
                index += 1
                continue
            status = entry[:2]
            path = entry[3:]
            if status[0] in {"R", "C"}:
                index += 1
                if index >= len(entries) or not entries[index]:
                    raise GitWorkspaceError("malformed rename/copy status")
                path = entries[index]
            paths.append(path)
            index += 1
        return tuple(sorted(set(paths)))

    @staticmethod
    def _matches(path: str, specs: Sequence[str]) -> bool:
        for raw in specs:
            spec = raw.strip("/")
            if spec.endswith("/**"):
                prefix = spec[:-3].rstrip("/")
                if path == prefix or path.startswith(prefix + "/"):
                    return True
            elif path == spec:
                return True
        return False

    def assert_changed_paths_authorized(self) -> tuple[str, ...]:
        changed = self.changed_paths()
        for path in changed:
            if not self._matches(path, self.task.scope.write):
                raise GitWorkspaceError(f"dirty path outside authorized write scope: {path}")
            if self._matches(path, self.task.scope.protected):
                raise GitWorkspaceError(f"protected path is dirty: {path}")
        return changed

    def commit_safe_checkpoint(self, *, message: str) -> tuple[Checkpoint, tuple[str, ...]]:
        changed = self.assert_changed_paths_authorized()
        if not changed:
            raise GitWorkspaceError("SAFE checkpoint requires an actual authorized change")
        self._git("add", "--all")
        self._git("commit", "-m", message)
        sha = self.current_sha()
        self._git("push", "origin", f"HEAD:refs/heads/{self.task.repository.task_branch}")
        self._git("push", "origin", f"{sha}:{self.safe_ref()}")
        checkpoint = Checkpoint(
            checkpoint_id=new_uuid7(),
            task_id=self.task.task_id,
            attempt_id=self.task.attempt_id,
            checkpoint_class=CheckpointClass.SAFE,
            commit_sha=sha,
            created_at=datetime.now(timezone.utc),
        )
        return checkpoint, changed

    def create_validation_evidence(
        self,
        *,
        subject_sha: str,
        validator_id: str,
        validator_version: str,
        stdout: str,
        stderr: str,
        passed: bool,
    ) -> ValidationEvidence:
        artifact = (stdout + "\n--- stderr ---\n" + stderr).encode("utf-8")
        return ValidationEvidence(
            evidence_id=new_uuid7(),
            task_id=self.task.task_id,
            subject_sha=subject_sha,
            validator_id=validator_id,
            validator_version=validator_version,
            result=EvidenceResult.PASS if passed else EvidenceResult.FAIL,
            observed_at=datetime.now(timezone.utc),
            artifact_sha256=(hashlib.sha256(artifact).hexdigest(),),
            summary="validator passed" if passed else "validator failed",
        )

    def promote_validated_checkpoint(
        self, *, safe: Checkpoint, evidence: ValidationEvidence
    ) -> Checkpoint:
        if evidence.result != EvidenceResult.PASS:
            raise GitWorkspaceError("cannot create VALIDATED checkpoint from failing evidence")
        if evidence.subject_sha != safe.commit_sha:
            raise GitWorkspaceError("validation evidence does not bind to SAFE commit")
        self._git("push", "origin", f"{safe.commit_sha}:{self.validated_ref()}")
        return Checkpoint(
            checkpoint_id=new_uuid7(),
            task_id=self.task.task_id,
            attempt_id=self.task.attempt_id,
            checkpoint_class=CheckpointClass.VALIDATED,
            commit_sha=safe.commit_sha,
            created_at=datetime.now(timezone.utc),
            evidence_ids=(evidence.evidence_id,),
        )

    def make_report(
        self,
        *,
        changed_files: tuple[str, ...],
        evidence: ValidationEvidence,
        safe: Checkpoint,
        validated: Checkpoint,
    ) -> WorkerReport:
        return WorkerReport(
            task_id=str(self.task.task_id),
            attempt_id=str(self.task.attempt_id),
            status="completed",
            changed_files=changed_files,
            validation_evidence=evidence,
            safe_checkpoint=safe,
            validated_checkpoint=validated,
        )

    def safe_ref(self) -> str:
        return f"refs/amodex/checkpoints/{self.task.task_id}/safe"

    def validated_ref(self) -> str:
        return f"refs/amodex/checkpoints/{self.task.task_id}/validated"

    def destroy(self) -> None:
        if self.path.exists():
            shutil.rmtree(self.path)
