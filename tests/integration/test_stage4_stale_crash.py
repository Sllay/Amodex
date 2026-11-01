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
    PromotionStatus,
)
from amodex.git.promotion import (
    CommandValidator,
    CrashPoint,
    FreshnessDisposition,
    InjectedPromotionCrash,
    LocalGitPromotionBackend,
    PromotionGate,
    StaleCandidateBlocked,
)
from amodex.storage.promotion import PostgresPromotionRepository


TARGET_REF = "refs/heads/main"
VALIDATOR = CommandValidator(
    validator_id="stage4-stale-unittest",
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
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
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
    files = {
        "a.py": "VALUE_A = 'a0'\n",
        "b.py": "VALUE_B = 'b0'\n",
        "contract.py": "API_LEVEL = 1\n",
        "consumer.py": "from contract import API_LEVEL\n\ndef supported():\n    return API_LEVEL == 1\n",
        "test_state.py": (
            "import unittest\n"
            "import a, b, consumer\n\n"
            "class StateTest(unittest.TestCase):\n"
            "    def test_state(self):\n"
            "        self.assertTrue(a.VALUE_A.startswith('a'))\n"
            "        self.assertTrue(b.VALUE_B.startswith('b'))\n"
            "        self.assertTrue(consumer.supported())\n"
        ),
    }
    for relative, content in files.items():
        path = work / relative
        path.write_text(content, encoding="utf-8")
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
) -> str:
    _git(fixture.work, "checkout", "-B", branch, base_sha)
    for relative, content in files.items():
        path = fixture.work / relative
        path.write_text(content, encoding="utf-8")
    _git(fixture.work, "add", "-A")
    _git(fixture.work, "commit", "-m", branch)
    sha = _git(fixture.work, "rev-parse", "HEAD")
    _git(fixture.work, "push", "-u", "origin", branch)
    return sha


def _make_candidate(
    gate: PromotionGate,
    *,
    source_sha: str,
    expected_target_sha: str,
    dependency_paths: tuple[str, ...] = (),
):
    task_id = new_uuid7()
    with gate.backend.checkout(source_sha) as checkout:
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
    return gate.create_candidate(
        checkpoint=checkpoint,
        source_evidence=(evidence,),
        expected_target_ref=TARGET_REF,
        expected_target_sha=expected_target_sha,
        dependency_paths=dependency_paths,
    )


@pytest.fixture()
def promotion_repository() -> PostgresPromotionRepository:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    sql = Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn) as conn:
        conn.execute(sql)
        conn.execute("TRUNCATE amodex_promotions, amodex_candidates")
    return PostgresPromotionRepository(dsn)


def test_stale_non_overlapping_candidate_reintegrates_but_contract_change_blocks(
    tmp_path: Path,
    promotion_repository: PostgresPromotionRepository,
) -> None:
    fixture = _make_repo(tmp_path)
    gate = PromotionGate(backend=fixture.backend, repository=promotion_repository)
    t0 = fixture.base_sha

    source_a = _commit_branch(
        fixture,
        branch="task/a",
        base_sha=t0,
        files={"a.py": "VALUE_A = 'a1'\n"},
    )
    candidate_a = _make_candidate(gate, source_sha=source_a, expected_target_sha=t0)

    source_b = _commit_branch(
        fixture,
        branch="task/b",
        base_sha=t0,
        files={"b.py": "VALUE_B = 'b1'\n"},
    )
    candidate_b = _make_candidate(gate, source_sha=source_b, expected_target_sha=t0)
    promoted_b = gate.promote(
        candidate_id=str(candidate_b.candidate_id),
        idempotency_key="stale/promote-b",
        validator=VALIDATOR,
    )
    t1 = promoted_b.promotion.target_post_sha
    assert t1 is not None and t1 != t0

    analysis_a = gate.analyze_freshness(candidate_a)
    assert analysis_a.disposition == FreshnessDisposition.SAFE_STALE
    assert "b.py" in analysis_a.target_changed_paths
    assert analysis_a.relevant_overlap == ()
    promoted_a = gate.promote(
        candidate_id=str(candidate_a.candidate_id),
        idempotency_key="stale/promote-a",
        validator=VALIDATOR,
    )
    t2 = promoted_a.promotion.target_post_sha
    assert promoted_a.promotion.status == PromotionStatus.PROMOTED
    assert t2 is not None and fixture.backend.is_ancestor(t1, t2)
    with fixture.backend.checkout(t2) as checkout:
        assert "a1" in (checkout / "a.py").read_text(encoding="utf-8")
        assert "b1" in (checkout / "b.py").read_text(encoding="utf-8")

    # The original authoring clone predates the integration commits. Refresh its
    # object database before creating the next task from the promoted target SHA.
    _git(fixture.work, "fetch", "origin", "main")

    source_consumer = _commit_branch(
        fixture,
        branch="task/consumer",
        base_sha=t2,
        files={
            "consumer.py": (
                "from contract import API_LEVEL\n\n"
                "def supported():\n"
                "    return API_LEVEL == 1\n\n"
                "def describe():\n"
                "    return f'consumer-api-{API_LEVEL}'\n"
            )
        },
    )
    candidate_consumer = _make_candidate(
        gate,
        source_sha=source_consumer,
        expected_target_sha=t2,
        dependency_paths=("contract.py",),
    )

    source_contract = _commit_branch(
        fixture,
        branch="task/contract-doc-compatible",
        base_sha=t2,
        files={"contract.py": "API_LEVEL = 1\nCONTRACT_REVISION = 2\n"},
    )
    candidate_contract = _make_candidate(
        gate,
        source_sha=source_contract,
        expected_target_sha=t2,
    )
    promoted_contract = gate.promote(
        candidate_id=str(candidate_contract.candidate_id),
        idempotency_key="stale/promote-contract",
        validator=VALIDATOR,
    )
    t3 = promoted_contract.promotion.target_post_sha
    assert t3 is not None

    analysis_consumer = gate.analyze_freshness(candidate_consumer)
    assert analysis_consumer.disposition == FreshnessDisposition.BLOCKED_STALE
    assert analysis_consumer.relevant_overlap == ("contract.py",)
    with pytest.raises(StaleCandidateBlocked):
        gate.promote(
            candidate_id=str(candidate_consumer.candidate_id),
            idempotency_key="stale/consumer-blocked",
            validator=VALIDATOR,
        )
    assert fixture.backend.read_ref(TARGET_REF) == t3
    stored = promotion_repository.get_candidate(str(candidate_consumer.candidate_id))
    assert stored is not None and stored.status == CandidateStatus.STALE


@pytest.mark.parametrize(
    "crash_point",
    (
        CrashPoint.AFTER_INTENT_BEFORE_REMOTE,
        CrashPoint.DURING_POST_INTEGRATION_VALIDATION,
        CrashPoint.AFTER_REMOTE_BEFORE_DB_ACK,
    ),
)
def test_promotion_crash_reconciles_exactly_once(
    tmp_path: Path,
    promotion_repository: PostgresPromotionRepository,
    crash_point: CrashPoint,
) -> None:
    fixture = _make_repo(tmp_path)
    gate = PromotionGate(backend=fixture.backend, repository=promotion_repository)
    source_sha = _commit_branch(
        fixture,
        branch=f"task/crash-{crash_point.value.lower()}",
        base_sha=fixture.base_sha,
        files={"a.py": f"VALUE_A = 'a-{crash_point.value}'\n"},
    )
    candidate = _make_candidate(
        gate,
        source_sha=source_sha,
        expected_target_sha=fixture.base_sha,
    )
    key = f"crash/{crash_point.value}"

    with pytest.raises(InjectedPromotionCrash):
        gate.promote(
            candidate_id=str(candidate.candidate_id),
            idempotency_key=key,
            validator=VALIDATOR,
            crash_at=crash_point,
        )
    assert promotion_repository.count_promotions_for_idempotency(key) == 1

    recovered = gate.reconcile(idempotency_key=key, validator=VALIDATOR)
    assert recovered.promotion.status == PromotionStatus.PROMOTED
    assert recovered.candidate.status == CandidateStatus.PROMOTED
    assert recovered.promotion.target_post_sha == fixture.backend.read_ref(TARGET_REF)
    assert recovered.promotion.reconciled_at is not None
    assert promotion_repository.count_promotions_for_idempotency(key) == 1

    repeated = gate.reconcile(idempotency_key=key, validator=VALIDATOR)
    assert repeated.promotion.promotion_id == recovered.promotion.promotion_id
    assert promotion_repository.count_promotions_for_idempotency(key) == 1
