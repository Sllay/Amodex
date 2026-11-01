"""Stage 4 exact-SHA Candidate/Promotion gate.

This module deliberately keeps promotion authority outside worker/Hermes code.
The deterministic backend used by the vertical slice is real Git against a bare
remote and uses ``git update-ref <ref> <new> <expected-old>`` as an atomic CAS.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator, Sequence

from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import (
    Candidate,
    CandidateStatus,
    Checkpoint,
    CheckpointClass,
    EvidenceResult,
    Promotion,
    PromotionMethod,
    PromotionStatus,
    ValidationEvidence,
)
from amodex.storage.promotion import PostgresPromotionRepository


class PromotionGateError(RuntimeError):
    pass


class HygieneViolation(PromotionGateError):
    pass


class IntegrationConflict(PromotionGateError):
    pass


class StaleCandidateBlocked(PromotionGateError):
    pass


class CompareAndSwapRejected(PromotionGateError):
    pass


class InjectedPromotionCrash(PromotionGateError):
    pass


class FreshnessDisposition(StrEnum):
    CURRENT = "CURRENT"
    SAFE_STALE = "SAFE_STALE"
    BLOCKED_STALE = "BLOCKED_STALE"
    HISTORY_REWRITTEN = "HISTORY_REWRITTEN"


class CrashPoint(StrEnum):
    AFTER_INTENT_BEFORE_REMOTE = "AFTER_INTENT_BEFORE_REMOTE"
    DURING_POST_INTEGRATION_VALIDATION = "DURING_POST_INTEGRATION_VALIDATION"
    AFTER_REMOTE_BEFORE_DB_ACK = "AFTER_REMOTE_BEFORE_DB_ACK"


@dataclass(frozen=True, slots=True)
class FreshnessAnalysis:
    disposition: FreshnessDisposition
    expected_target_sha: str
    observed_target_sha: str
    target_changed_paths: tuple[str, ...]
    relevant_overlap: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PromotionOutcome:
    candidate: Candidate
    promotion: Promotion
    freshness: FreshnessAnalysis


@dataclass(frozen=True, slots=True)
class CommandValidator:
    validator_id: str
    validator_version: str
    command: tuple[str, ...]
    timeout_seconds: int = 120

    def __call__(self, checkout: Path, *, task_id: str, subject_sha: str) -> ValidationEvidence:
        completed = subprocess.run(
            list(self.command),
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        artifact = (stdout + "\n--- stderr ---\n" + stderr).encode("utf-8")
        return ValidationEvidence(
            evidence_id=new_uuid7(),
            task_id=task_id,
            subject_sha=subject_sha,
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            result=EvidenceResult.PASS if completed.returncode == 0 else EvidenceResult.FAIL,
            observed_at=datetime.now(timezone.utc),
            artifact_sha256=(hashlib.sha256(artifact).hexdigest(),),
            summary=(
                f"command passed: {' '.join(self.command)}"
                if completed.returncode == 0
                else f"command failed ({completed.returncode}): {' '.join(self.command)}"
            ),
        )


PostIntegrationValidator = Callable[..., ValidationEvidence]


class LocalGitPromotionBackend:
    """Real Git backend for a local/bare remote repository.

    It is intentionally not a mock. Integration commits are created by Git,
    persisted under namespaced remote refs, and protected target mutation is an
    atomic compare-and-swap using the target's expected old SHA.
    """

    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()
        if not self.repository.exists():
            raise ValueError(f"repository does not exist: {self.repository}")
        self._run("rev-parse", "--git-dir")

    def _run(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = ["git"]
        if cwd is None:
            argv.extend(("-C", str(self.repository)))
        argv.extend(args)
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and completed.returncode != 0:
            raise PromotionGateError(
                f"git failed ({completed.returncode}): {' '.join(argv)}\n{completed.stderr[-4000:]}"
            )
        return completed

    @staticmethod
    def _run_checkout(
        checkout: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and completed.returncode != 0:
            raise PromotionGateError(
                f"git failed ({completed.returncode}): git {' '.join(args)}\n"
                f"{completed.stderr[-4000:]}"
            )
        return completed

    def read_ref(self, ref: str) -> str:
        completed = self._run("rev-parse", "--verify", ref)
        return completed.stdout.strip()

    def read_optional_ref(self, ref: str) -> str | None:
        completed = self._run("rev-parse", "--verify", ref, check=False)
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    def commit_exists(self, sha: str) -> bool:
        completed = self._run("cat-file", "-e", f"{sha}^{{commit}}", check=False)
        return completed.returncode == 0

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        completed = self._run("merge-base", "--is-ancestor", ancestor, descendant, check=False)
        if completed.returncode == 0:
            return True
        if completed.returncode == 1:
            return False
        raise PromotionGateError(completed.stderr.strip() or "git merge-base failed")

    def changed_paths(self, old_sha: str, new_sha: str) -> tuple[str, ...]:
        completed = self._run("diff", "--name-only", "--no-renames", old_sha, new_sha)
        return tuple(sorted({line for line in completed.stdout.splitlines() if line}))

    def file_size(self, sha: str, path: str) -> int:
        completed = self._run("cat-file", "-s", f"{sha}:{path}")
        return int(completed.stdout.strip())

    def file_bytes(self, sha: str, path: str) -> bytes:
        completed = subprocess.run(
            ["git", "-C", str(self.repository), "show", f"{sha}:{path}"],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise PromotionGateError(
                f"cannot read {path!r} at {sha}: {completed.stderr.decode('utf-8', 'replace')[-2000:]}"
            )
        return completed.stdout

    @contextmanager
    def checkout(self, sha: str) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="amodex-stage4-checkout-") as raw:
            path = Path(raw)
            completed = subprocess.run(
                ["git", "clone", "--no-checkout", str(self.repository), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise PromotionGateError(completed.stderr[-4000:])
            self._run_checkout(path, "checkout", "--detach", sha)
            if self._run_checkout(path, "status", "--porcelain").stdout.strip():
                raise PromotionGateError("fresh integration checkout is dirty")
            yield path

    def create_integration_commit(
        self,
        *,
        promotion_id: str,
        target_sha: str,
        source_sha: str,
    ) -> str:
        ref = self.integration_ref(promotion_id)
        existing = self.read_optional_ref(ref)
        if existing is not None:
            return existing
        with tempfile.TemporaryDirectory(prefix="amodex-stage4-integration-") as raw:
            path = Path(raw)
            completed = subprocess.run(
                ["git", "clone", "--no-checkout", str(self.repository), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise PromotionGateError(completed.stderr[-4000:])
            self._run_checkout(path, "checkout", "--detach", target_sha)
            self._run_checkout(path, "config", "user.name", "Amodex Promotion Gate")
            self._run_checkout(path, "config", "user.email", "promotion@amodex.invalid")
            merged = self._run_checkout(
                path,
                "merge",
                "--no-ff",
                "--no-commit",
                source_sha,
                check=False,
            )
            if merged.returncode != 0:
                self._run_checkout(path, "merge", "--abort", check=False)
                raise IntegrationConflict(merged.stderr[-4000:] or "candidate merge conflicted")
            self._run_checkout(
                path,
                "commit",
                "-m",
                f"Amodex integration {promotion_id}",
            )
            integration_sha = self._run_checkout(path, "rev-parse", "HEAD").stdout.strip()
            pushed = self._run_checkout(
                path,
                "push",
                "origin",
                f"{integration_sha}:{ref}",
                check=False,
            )
            if pushed.returncode != 0:
                remote = self.read_optional_ref(ref)
                if remote != integration_sha:
                    raise PromotionGateError(pushed.stderr[-4000:] or "integration ref push failed")
            return integration_sha

    def create_revert_commit(
        self,
        *,
        target_sha: str,
        commit_to_revert: str,
        durable_ref: str,
    ) -> str:
        existing = self.read_optional_ref(durable_ref)
        if existing is not None:
            return existing
        with tempfile.TemporaryDirectory(prefix="amodex-stage4-revert-") as raw:
            path = Path(raw)
            completed = subprocess.run(
                ["git", "clone", "--no-checkout", str(self.repository), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise PromotionGateError(completed.stderr[-4000:])
            self._run_checkout(path, "checkout", "--detach", target_sha)
            self._run_checkout(path, "config", "user.name", "Amodex Promotion Gate")
            self._run_checkout(path, "config", "user.email", "promotion@amodex.invalid")
            reverted = self._run_checkout(path, "revert", "--no-edit", commit_to_revert, check=False)
            if reverted.returncode != 0:
                self._run_checkout(path, "revert", "--abort", check=False)
                raise IntegrationConflict(reverted.stderr[-4000:] or "revert conflicted")
            sha = self._run_checkout(path, "rev-parse", "HEAD").stdout.strip()
            self._run_checkout(path, "push", "origin", f"{sha}:{durable_ref}")
            return sha

    def compare_and_swap_ref(self, *, ref: str, expected_sha: str, new_sha: str) -> None:
        completed = self._run("update-ref", ref, new_sha, expected_sha, check=False)
        if completed.returncode != 0:
            observed = self.read_optional_ref(ref)
            raise CompareAndSwapRejected(
                f"CAS rejected for {ref}: expected {expected_sha}, observed {observed}"
            )
        observed = self.read_ref(ref)
        if observed != new_sha:
            raise CompareAndSwapRejected(
                f"CAS acknowledgement mismatch for {ref}: expected new {new_sha}, observed {observed}"
            )

    @staticmethod
    def integration_ref(promotion_id: str) -> str:
        return f"refs/amodex/integrations/{promotion_id}"


_SECRET_PATH_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "service-account.json",
    "id_rsa",
    "id_ed25519",
}
_GENERATED_PARTS = {
    ".godot",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
_SECRET_CONTENT_RE = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}"
)
_ABSOLUTE_PATH_RE = re.compile(rb"(?:/home/[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9_. -]+\\)")


def scan_repository_hygiene(
    backend: LocalGitPromotionBackend,
    *,
    subject_sha: str,
    changed_paths: Sequence[str],
    max_changed_file_bytes: int = 5 * 1024 * 1024,
) -> None:
    violations: list[str] = []
    for path in changed_paths:
        pure_parts = tuple(part.lower() for part in Path(path).parts)
        name = Path(path).name.lower()
        if name in _SECRET_PATH_NAMES or name.endswith((".pem", ".key", ".p12", ".pfx")):
            violations.append(f"secret-like path: {path}")
            continue
        if any(part in _GENERATED_PARTS for part in pure_parts):
            violations.append(f"generated/cache path: {path}")
            continue
        try:
            size = backend.file_size(subject_sha, path)
        except PromotionGateError:
            # Deleted paths have no subject blob and are valid hygiene-wise.
            continue
        if size > max_changed_file_bytes:
            violations.append(f"large changed file ({size} bytes): {path}")
            continue
        data = backend.file_bytes(subject_sha, path)
        if b"\x00" in data:
            continue
        if _SECRET_CONTENT_RE.search(data):
            violations.append(f"secret-like content: {path}")
        if _ABSOLUTE_PATH_RE.search(data):
            violations.append(f"local absolute path: {path}")
    if violations:
        raise HygieneViolation("; ".join(violations))


def _scope_matches(path: str, spec: str) -> bool:
    clean = spec.strip("/")
    if clean.endswith("/**"):
        prefix = clean[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if clean.endswith("/*"):
        prefix = clean[:-2].rstrip("/")
        if not path.startswith(prefix + "/"):
            return False
        return "/" not in path[len(prefix) + 1 :]
    return path == clean


def _relevant_overlap(
    changed: Sequence[str],
    candidate_paths: Sequence[str],
) -> tuple[str, ...]:
    overlap: set[str] = set()
    for target_path in changed:
        for candidate_path in candidate_paths:
            if _scope_matches(target_path, candidate_path) or _scope_matches(candidate_path, target_path):
                overlap.add(target_path)
    return tuple(sorted(overlap))


class PromotionGate:
    def __init__(
        self,
        *,
        backend: LocalGitPromotionBackend,
        repository: PostgresPromotionRepository,
    ) -> None:
        self.backend = backend
        self.repository = repository

    def create_candidate(
        self,
        *,
        checkpoint: Checkpoint,
        source_evidence: Sequence[ValidationEvidence],
        expected_target_ref: str,
        expected_target_sha: str,
        dependency_paths: Sequence[str] = (),
    ) -> Candidate:
        if checkpoint.checkpoint_class != CheckpointClass.VALIDATED:
            raise PromotionGateError("Candidate requires a VALIDATED checkpoint")
        if not source_evidence:
            raise PromotionGateError("Candidate requires source evidence")
        evidence_by_id = {str(item.evidence_id): item for item in source_evidence}
        for evidence_id in checkpoint.evidence_ids:
            evidence = evidence_by_id.get(str(evidence_id))
            if evidence is None:
                raise PromotionGateError(f"missing checkpoint evidence {evidence_id}")
            if evidence.result != EvidenceResult.PASS:
                raise PromotionGateError("Candidate source evidence must pass")
            if evidence.subject_sha != checkpoint.commit_sha:
                raise PromotionGateError("Candidate evidence is not bound to checkpoint SHA")
        if not self.backend.commit_exists(checkpoint.commit_sha):
            raise PromotionGateError("validated checkpoint commit is absent from repository authority")
        if not self.backend.commit_exists(expected_target_sha):
            raise PromotionGateError("expected target commit is absent from repository authority")
        changed_paths = self.backend.changed_paths(expected_target_sha, checkpoint.commit_sha)
        if not changed_paths:
            raise PromotionGateError("Candidate has no repository change relative to expected target")
        scan_repository_hygiene(
            self.backend,
            subject_sha=checkpoint.commit_sha,
            changed_paths=changed_paths,
        )
        candidate = Candidate(
            candidate_id=new_uuid7(),
            task_id=checkpoint.task_id,
            validated_checkpoint_id=checkpoint.checkpoint_id,
            source_sha=checkpoint.commit_sha,
            expected_target_ref=expected_target_ref,
            expected_target_sha=expected_target_sha,
            changed_paths=changed_paths,
            dependency_paths=tuple(sorted(set(dependency_paths))),
            evidence_ids=tuple(checkpoint.evidence_ids),
            status=CandidateStatus.READY,
            created_at=datetime.now(timezone.utc),
        )
        return self.repository.create_candidate(candidate)

    def analyze_freshness(self, candidate: Candidate) -> FreshnessAnalysis:
        observed = self.backend.read_ref(candidate.expected_target_ref)
        if observed == candidate.expected_target_sha:
            return FreshnessAnalysis(
                disposition=FreshnessDisposition.CURRENT,
                expected_target_sha=candidate.expected_target_sha,
                observed_target_sha=observed,
                target_changed_paths=(),
                relevant_overlap=(),
            )
        if not self.backend.is_ancestor(candidate.expected_target_sha, observed):
            return FreshnessAnalysis(
                disposition=FreshnessDisposition.HISTORY_REWRITTEN,
                expected_target_sha=candidate.expected_target_sha,
                observed_target_sha=observed,
                target_changed_paths=(),
                relevant_overlap=(),
            )
        target_changed = self.backend.changed_paths(candidate.expected_target_sha, observed)
        overlap = _relevant_overlap(
            target_changed,
            (*candidate.changed_paths, *candidate.dependency_paths),
        )
        return FreshnessAnalysis(
            disposition=(
                FreshnessDisposition.BLOCKED_STALE
                if overlap
                else FreshnessDisposition.SAFE_STALE
            ),
            expected_target_sha=candidate.expected_target_sha,
            observed_target_sha=observed,
            target_changed_paths=target_changed,
            relevant_overlap=overlap,
        )

    def promote(
        self,
        *,
        candidate_id: str,
        idempotency_key: str,
        validator: PostIntegrationValidator,
        method: PromotionMethod = PromotionMethod.MERGE,
        crash_at: CrashPoint | None = None,
    ) -> PromotionOutcome:
        candidate = self.repository.get_candidate(candidate_id)
        if candidate is None:
            raise PromotionGateError(f"unknown candidate {candidate_id}")
        if candidate.status not in {CandidateStatus.READY, CandidateStatus.STALE}:
            raise PromotionGateError(f"candidate is not promotable: {candidate.status.value}")

        existing = self.repository.get_promotion_by_idempotency(idempotency_key)
        if existing is not None:
            return self.reconcile(
                idempotency_key=idempotency_key,
                validator=validator,
                crash_at=crash_at,
            )

        freshness = self.analyze_freshness(candidate)
        if freshness.disposition == FreshnessDisposition.HISTORY_REWRITTEN:
            self.repository.set_candidate_status(
                candidate_id=str(candidate.candidate_id),
                status=CandidateStatus.BLOCKED,
            )
            raise StaleCandidateBlocked("target history no longer descends from candidate base")
        if freshness.disposition == FreshnessDisposition.BLOCKED_STALE:
            self.repository.set_candidate_status(
                candidate_id=str(candidate.candidate_id),
                status=CandidateStatus.STALE,
            )
            raise StaleCandidateBlocked(
                f"target changed relevant paths: {freshness.relevant_overlap!r}"
            )

        now = datetime.now(timezone.utc)
        promotion = Promotion(
            promotion_id=new_uuid7(),
            candidate_id=candidate.candidate_id,
            idempotency_key=idempotency_key,
            target_ref=candidate.expected_target_ref,
            expected_target_sha=freshness.observed_target_sha,
            method=method,
            status=PromotionStatus.INTENT_PERSISTED,
            created_at=now,
            updated_at=now,
        )
        promotion = self.repository.create_promotion_intent(promotion)

        if crash_at == CrashPoint.AFTER_INTENT_BEFORE_REMOTE:
            raise InjectedPromotionCrash(crash_at.value)

        return self._continue_promotion(
            candidate=candidate,
            promotion=promotion,
            freshness=freshness,
            validator=validator,
            crash_at=crash_at,
        )

    def _continue_promotion(
        self,
        *,
        candidate: Candidate,
        promotion: Promotion,
        freshness: FreshnessAnalysis,
        validator: PostIntegrationValidator,
        crash_at: CrashPoint | None,
    ) -> PromotionOutcome:
        integration_ref = self.backend.integration_ref(str(promotion.promotion_id))
        integration_sha = self.backend.read_optional_ref(integration_ref)
        if integration_sha is None:
            integration_sha = self.backend.create_integration_commit(
                promotion_id=str(promotion.promotion_id),
                target_sha=promotion.expected_target_sha,
                source_sha=candidate.source_sha,
            )

        if crash_at == CrashPoint.DURING_POST_INTEGRATION_VALIDATION:
            raise InjectedPromotionCrash(crash_at.value)

        if promotion.status == PromotionStatus.INTENT_PERSISTED:
            with self.backend.checkout(integration_sha) as checkout:
                evidence = validator(
                    checkout,
                    task_id=str(candidate.task_id),
                    subject_sha=integration_sha,
                )
            if evidence.subject_sha != integration_sha:
                raise PromotionGateError("post-integration evidence is bound to the wrong SHA")
            if evidence.result != EvidenceResult.PASS:
                promotion = self.repository.transition_promotion(
                    promotion_id=str(promotion.promotion_id),
                    expected_statuses=(PromotionStatus.INTENT_PERSISTED,),
                    status=PromotionStatus.BLOCKED,
                    integration_sha=integration_sha,
                    detail=evidence.summary,
                )
                self.repository.set_candidate_status(
                    candidate_id=str(candidate.candidate_id),
                    status=CandidateStatus.BLOCKED,
                )
                return PromotionOutcome(candidate, promotion, freshness)
            promotion = self.repository.transition_promotion(
                promotion_id=str(promotion.promotion_id),
                expected_statuses=(PromotionStatus.INTENT_PERSISTED,),
                status=PromotionStatus.INTEGRATION_VALIDATED,
                integration_sha=integration_sha,
                integration_evidence_ids=(str(evidence.evidence_id),),
                detail=evidence.summary,
            )

        observed_target = self.backend.read_ref(promotion.target_ref)
        if observed_target == integration_sha:
            promotion = self.repository.transition_promotion(
                promotion_id=str(promotion.promotion_id),
                expected_statuses=(
                    PromotionStatus.INTEGRATION_VALIDATED,
                    PromotionStatus.UNKNOWN_OUTCOME,
                    PromotionStatus.REMOTE_APPLIED,
                ),
                status=PromotionStatus.PROMOTED,
                integration_sha=integration_sha,
                target_post_sha=integration_sha,
                reconciled=True,
                detail="remote target already equals validated integration SHA",
            )
            candidate = self.repository.set_candidate_status(
                candidate_id=str(candidate.candidate_id),
                status=CandidateStatus.PROMOTED,
            )
            return PromotionOutcome(candidate, promotion, freshness)
        if observed_target != promotion.expected_target_sha:
            promotion = self.repository.transition_promotion(
                promotion_id=str(promotion.promotion_id),
                expected_statuses=(
                    PromotionStatus.INTEGRATION_VALIDATED,
                    PromotionStatus.UNKNOWN_OUTCOME,
                ),
                status=PromotionStatus.BLOCKED,
                integration_sha=integration_sha,
                detail=(
                    "target advanced after integration validation: "
                    f"expected {promotion.expected_target_sha}, observed {observed_target}"
                ),
            )
            candidate = self.repository.set_candidate_status(
                candidate_id=str(candidate.candidate_id),
                status=CandidateStatus.STALE,
            )
            return PromotionOutcome(candidate, promotion, freshness)

        self.backend.compare_and_swap_ref(
            ref=promotion.target_ref,
            expected_sha=promotion.expected_target_sha,
            new_sha=integration_sha,
        )

        if crash_at == CrashPoint.AFTER_REMOTE_BEFORE_DB_ACK:
            raise InjectedPromotionCrash(crash_at.value)

        promotion = self.repository.transition_promotion(
            promotion_id=str(promotion.promotion_id),
            expected_statuses=(PromotionStatus.INTEGRATION_VALIDATED,),
            status=PromotionStatus.REMOTE_APPLIED,
            integration_sha=integration_sha,
            target_post_sha=integration_sha,
            detail="atomic expected-SHA target update accepted",
        )
        promotion = self.repository.transition_promotion(
            promotion_id=str(promotion.promotion_id),
            expected_statuses=(PromotionStatus.REMOTE_APPLIED,),
            status=PromotionStatus.PROMOTED,
            integration_sha=integration_sha,
            target_post_sha=integration_sha,
            reconciled=True,
            detail="promotion complete and remote state reconciled",
        )
        candidate = self.repository.set_candidate_status(
            candidate_id=str(candidate.candidate_id),
            status=CandidateStatus.PROMOTED,
        )
        return PromotionOutcome(candidate, promotion, freshness)

    def reconcile(
        self,
        *,
        idempotency_key: str,
        validator: PostIntegrationValidator,
        crash_at: CrashPoint | None = None,
    ) -> PromotionOutcome:
        promotion = self.repository.get_promotion_by_idempotency(idempotency_key)
        if promotion is None:
            raise PromotionGateError(f"no promotion intent for {idempotency_key}")
        candidate = self.repository.get_candidate(str(promotion.candidate_id))
        if candidate is None:
            raise PromotionGateError("promotion refers to missing candidate")
        if promotion.status == PromotionStatus.PROMOTED:
            freshness = FreshnessAnalysis(
                disposition=FreshnessDisposition.CURRENT,
                expected_target_sha=candidate.expected_target_sha,
                observed_target_sha=promotion.expected_target_sha,
                target_changed_paths=(),
                relevant_overlap=(),
            )
            return PromotionOutcome(candidate, promotion, freshness)
        if promotion.status == PromotionStatus.BLOCKED:
            freshness = self.analyze_freshness(candidate)
            return PromotionOutcome(candidate, promotion, freshness)

        integration_ref = self.backend.integration_ref(str(promotion.promotion_id))
        integration_sha = self.backend.read_optional_ref(integration_ref)
        if integration_sha is not None and self.backend.read_ref(promotion.target_ref) == integration_sha:
            if not promotion.integration_evidence_ids:
                with self.backend.checkout(integration_sha) as checkout:
                    evidence = validator(
                        checkout,
                        task_id=str(candidate.task_id),
                        subject_sha=integration_sha,
                    )
                if evidence.result != EvidenceResult.PASS:
                    raise PromotionGateError(
                        "remote target equals integration SHA but recovery validation failed"
                    )
                promotion = self.repository.transition_promotion(
                    promotion_id=str(promotion.promotion_id),
                    expected_statuses=(
                        PromotionStatus.INTENT_PERSISTED,
                        PromotionStatus.UNKNOWN_OUTCOME,
                    ),
                    status=PromotionStatus.INTEGRATION_VALIDATED,
                    integration_sha=integration_sha,
                    target_post_sha=integration_sha,
                    integration_evidence_ids=(str(evidence.evidence_id),),
                    reconciled=True,
                    detail="recovered integration evidence after uncertain remote outcome",
                )
            promotion = self.repository.transition_promotion(
                promotion_id=str(promotion.promotion_id),
                expected_statuses=(
                    PromotionStatus.INTEGRATION_VALIDATED,
                    PromotionStatus.UNKNOWN_OUTCOME,
                    PromotionStatus.REMOTE_APPLIED,
                ),
                status=PromotionStatus.PROMOTED,
                integration_sha=integration_sha,
                target_post_sha=integration_sha,
                reconciled=True,
                detail="reconciled already-applied remote target",
            )
            candidate = self.repository.set_candidate_status(
                candidate_id=str(candidate.candidate_id),
                status=CandidateStatus.PROMOTED,
            )
            freshness = FreshnessAnalysis(
                disposition=FreshnessDisposition.CURRENT,
                expected_target_sha=candidate.expected_target_sha,
                observed_target_sha=promotion.expected_target_sha,
                target_changed_paths=(),
                relevant_overlap=(),
            )
            return PromotionOutcome(candidate, promotion, freshness)

        freshness = self.analyze_freshness(candidate)
        if freshness.disposition in {
            FreshnessDisposition.HISTORY_REWRITTEN,
            FreshnessDisposition.BLOCKED_STALE,
        } and self.backend.read_ref(promotion.target_ref) != promotion.expected_target_sha:
            promotion = self.repository.transition_promotion(
                promotion_id=str(promotion.promotion_id),
                expected_statuses=(
                    PromotionStatus.INTENT_PERSISTED,
                    PromotionStatus.INTEGRATION_VALIDATED,
                    PromotionStatus.UNKNOWN_OUTCOME,
                ),
                status=PromotionStatus.BLOCKED,
                integration_sha=integration_sha,
                detail="reconciliation observed a new incompatible target state",
                reconciled=True,
            )
            candidate = self.repository.set_candidate_status(
                candidate_id=str(candidate.candidate_id),
                status=CandidateStatus.STALE,
            )
            return PromotionOutcome(candidate, promotion, freshness)

        return self._continue_promotion(
            candidate=candidate,
            promotion=promotion,
            freshness=freshness,
            validator=validator,
            crash_at=crash_at,
        )
