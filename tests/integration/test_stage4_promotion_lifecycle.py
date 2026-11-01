from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest

from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import (
    CandidateStatus,
    Checkpoint,
    CheckpointClass,
    EvidenceResult,
    PromotionMethod,
    PromotionStatus,
)
from amodex.git.promotion import (
    CommandValidator,
    HygieneViolation,
    LocalGitPromotionBackend,
    PromotionGate,
)
from amodex.storage.promotion import PostgresPromotionRepository


TARGET_REF = "refs/heads/main"
VALIDATOR = CommandValidator(
    validator_id="stage4-unittest",
    validator_version="1",
    command=("python", "-m", "unittest", "-q"),
)


@dataclass(frozen=True, slots=True)
class GitFixture:
    remote: Path
    work: Path
    base_sha: str
    backend: LocalGitPromotionBackend


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({completed.returncode})\n{completed.stderr}"
        )
    return completed.stdout.strip()


def _make_repo(tmp_path: Path) -> GitFixture:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", str(work))
    _git(work, "config", "user.name", "Stage4 Test")
    _git(work, "config", "user.email", "stage4@example.invalid")
    (work / "calc.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (work / "test_calc.py").write_text(
        "import unittest\n"
        "import calc\n\n"
        "class CalcTest(unittest.TestCase):\n"
        "    def test_value(self):\n"
        "        self.assertEqual(calc.value(), 1)\n",
        encoding="utf-8",
    )
    _git(work, "add", ".")
    _git(work, "commit", "-m", "base")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")
    base_sha = _git(work, "rev-parse", "HEAD")
    return GitFixture(remote, work, base_sha, LocalGitPromotionBackend(remote))


def _commit_branch(
    fixture: GitFixture,
    *,
    branch: str,
    base_sha: str,
    files: dict[str, str],
    message: str,
) -> str:
    _git(fixture.work, "checkout", "-B", branch, base_sha)
    for relative, content in files.items():
        path = fixture.work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(fixture.work, "add", "-A")
    _git(fixture.work, "commit", "-m", message)
    sha = _git(fixture.work, "rev-parse", "HEAD")
    _git(fixture.work, "push", "-u", "origin", branch)
    return sha


def _validated_checkpoint(
    backend: LocalGitPromotionBackend,
    *,
    source_sha: str,
) -> tuple[Checkpoint, object]:
    task_id = new_uuid7()
    with backend.checkout(source_sha) as checkout:
        evidence = VALIDATOR(checkout, task_id=task_id, subject_sha=source_sha)
    assert evidence.result == EvidenceResult.PASS
    checkpoint = Checkpoint(
        checkpoint_id=new_uuid7(),
        task_id=task_id,
        attempt_id=new_uuid7(),
        checkpoint_class=CheckpointClass.VALIDATED,
        commit_sha=source_sha,
        created_at=datetime.now(timezone.utc),
        evidence_ids=(evidence.evidence_id,),
    )
    return checkpoint, evidence


@pytest.fixture()
def promotion_repository() -> PostgresPromotionRepository:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    sql = Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn) as conn:
        conn.execute(sql)
        conn.execute("TRUNCATE amodex_promotions, amodex_candidates")
    return PostgresPromotionRepository(dsn)


def test_candidate_exact_sha_promotion_idempotency_and_revert(
    tmp_path: Path,
    promotion_repository: PostgresPromotionRepository,
) -> None:
    fixture = _make_repo(tmp_path)
    source_sha = _commit_branch(
        fixture,
        branch="task/change-value",
        base_sha=fixture.base_sha,
        files={
            "calc.py": "def value():\n    return 2\n",
            "test_calc.py": (
                "import unittest\n"
                "import calc\n\n"
                "class CalcTest(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(calc.value(), 2)\n"
            ),
        },
        message="change value",
    )
    checkpoint, evidence = _validated_checkpoint(fixture.backend, source_sha=source_sha)
    gate = PromotionGate(backend=fixture.backend, repository=promotion_repository)
    candidate = gate.create_candidate(
        checkpoint=checkpoint,
        source_evidence=(evidence,),
        expected_target_ref=TARGET_REF,
        expected_target_sha=fixture.base_sha,
    )
    assert candidate.source_sha == source_sha
    assert set(candidate.changed_paths) == {"calc.py", "test_calc.py"}

    outcome = gate.promote(
        candidate_id=str(candidate.candidate_id),
        idempotency_key="promotion/change-value",
        validator=VALIDATOR,
    )
    assert outcome.promotion.status == PromotionStatus.PROMOTED
    assert outcome.candidate.status == CandidateStatus.PROMOTED
    assert outcome.promotion.integration_sha is not None
    assert outcome.promotion.integration_sha != candidate.source_sha
    assert outcome.promotion.target_post_sha == outcome.promotion.integration_sha
    assert fixture.backend.read_ref(TARGET_REF) == outcome.promotion.integration_sha
    assert outcome.promotion.integration_evidence_ids
    assert promotion_repository.count_promotions_for_idempotency("promotion/change-value") == 1

    repeated = gate.promote(
        candidate_id=str(candidate.candidate_id),
        idempotency_key="promotion/change-value",
        validator=VALIDATOR,
    )
    assert repeated.promotion.promotion_id == outcome.promotion.promotion_id
    assert promotion_repository.count_promotions_for_idempotency("promotion/change-value") == 1

    # Rollback is a new history-preserving commit, never a reset/force rewrite.
    promoted_sha = outcome.promotion.target_post_sha
    assert promoted_sha is not None
    _git(fixture.work, "fetch", "origin", "main")
    _git(fixture.work, "checkout", "-B", "task/revert-value", "origin/main")
    _git(fixture.work, "revert", "-m", "1", "--no-edit", promoted_sha)
    revert_sha = _git(fixture.work, "rev-parse", "HEAD")
    _git(fixture.work, "push", "-u", "origin", "task/revert-value")
    revert_checkpoint, revert_evidence = _validated_checkpoint(
        fixture.backend,
        source_sha=revert_sha,
    )
    revert_candidate = gate.create_candidate(
        checkpoint=revert_checkpoint,
        source_evidence=(revert_evidence,),
        expected_target_ref=TARGET_REF,
        expected_target_sha=promoted_sha,
    )
    rollback = gate.promote(
        candidate_id=str(revert_candidate.candidate_id),
        idempotency_key="promotion/revert-value",
        validator=VALIDATOR,
        method=PromotionMethod.REVERT,
    )
    assert rollback.promotion.status == PromotionStatus.PROMOTED
    rollback_sha = rollback.promotion.target_post_sha
    assert rollback_sha is not None
    assert fixture.backend.is_ancestor(promoted_sha, rollback_sha)
    with fixture.backend.checkout(rollback_sha) as checkout:
        assert "return 1" in (checkout / "calc.py").read_text(encoding="utf-8")
        rollback_evidence = VALIDATOR(
            checkout,
            task_id=str(revert_checkpoint.task_id),
            subject_sha=rollback_sha,
        )
    assert rollback_evidence.result == EvidenceResult.PASS


def test_candidate_hygiene_rejects_secret_like_repository_change(
    tmp_path: Path,
    promotion_repository: PostgresPromotionRepository,
) -> None:
    fixture = _make_repo(tmp_path)
    source_sha = _commit_branch(
        fixture,
        branch="task/bad-secret",
        base_sha=fixture.base_sha,
        files={".env": "API_KEY=abcdefghijklmnopqrstuvwxyz123456\n"},
        message="accidental secret",
    )
    checkpoint, evidence = _validated_checkpoint(fixture.backend, source_sha=source_sha)
    gate = PromotionGate(backend=fixture.backend, repository=promotion_repository)
    with pytest.raises(HygieneViolation):
        gate.create_candidate(
            checkpoint=checkpoint,
            source_evidence=(evidence,),
            expected_target_ref=TARGET_REF,
            expected_target_sha=fixture.base_sha,
        )
    assert fixture.backend.read_ref(TARGET_REF) == fixture.base_sha
