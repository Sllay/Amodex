from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import Checkpoint, CheckpointClass, EvidenceResult
from amodex.git.promotion import CommandValidator, LocalGitPromotionBackend, PromotionGate
from amodex.product.promotion import ConfiguredPromotionExecutor
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.promotion import PostgresPromotionRepository
from amodex.storage.routing import PostgresRoutingRepository


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _prepare_git(tmp_path: Path) -> tuple[Path, Path, str, str]:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", str(work))
    _git(work, "config", "user.name", "Stage6 Test")
    _git(work, "config", "user.email", "stage6@example.invalid")
    (work / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (work / "test_value.py").write_text(
        "import unittest\nimport value\n\n"
        "class ValueTest(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(value.VALUE, 1)\n",
        encoding="utf-8",
    )
    _git(work, "add", ".")
    _git(work, "commit", "-m", "base")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")
    base_sha = _git(work, "rev-parse", "HEAD")

    _git(work, "checkout", "-b", "task/change")
    (work / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
    (work / "test_value.py").write_text(
        "import unittest\nimport value\n\n"
        "class ValueTest(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(value.VALUE, 2)\n",
        encoding="utf-8",
    )
    _git(work, "add", ".")
    _git(work, "commit", "-m", "change")
    source_sha = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "-u", "origin", "task/change")
    return remote, work, base_sha, source_sha


def _migrate(dsn: str) -> tuple[PostgresRunRepository, PostgresProductRepository]:
    runs = PostgresRunRepository(dsn)
    runs.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8"))
    PostgresRoutingRepository(dsn).migrate(Path("migrations/stage5/0001_routing_measurements.sql"))
    product = PostgresProductRepository(dsn)
    product.migrate(Path("migrations/stage6/0001_product_surfaces.sql"))
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE amodex_audit_events, amodex_promotions, amodex_candidates CASCADE")
    return runs, product


def test_product_promote_endpoint_executes_real_exact_sha_gate(tmp_path: Path) -> None:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    runs, product = _migrate(dsn)
    remote, _, base_sha, source_sha = _prepare_git(tmp_path)
    backend = LocalGitPromotionBackend(remote)
    promotion_repo = PostgresPromotionRepository(dsn)
    validator = CommandValidator(
        validator_id="stage6-source",
        validator_version="1",
        command=(sys.executable, "-m", "unittest", "-q"),
    )
    task_id = new_uuid7()
    with backend.checkout(source_sha) as checkout:
        evidence = validator(checkout, task_id=task_id, subject_sha=source_sha)
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
    candidate = PromotionGate(backend=backend, repository=promotion_repo).create_candidate(
        checkpoint=checkpoint,
        source_evidence=(evidence,),
        expected_target_ref="refs/heads/main",
        expected_target_sha=base_sha,
    )

    executor = ConfiguredPromotionExecutor(
        repository_path=remote,
        dsn=dsn,
        validator_command=(sys.executable, "-m", "unittest", "-q"),
    )
    app = create_app(runs, product_repository=product, promotion_executor=executor)
    with TestClient(app) as client:
        promoted = client.post(f"/v1/candidates/{candidate.candidate_id}/promote")
        assert promoted.status_code == 200, promoted.text
        body = promoted.json()
        assert body["status"] == "PROMOTED"
        assert body["freshness"] == "CURRENT"
        assert body["target_post_sha"] == backend.read_ref("refs/heads/main")
        audit = client.get("/v1/audit").json()
        assert any(item["action"] == "PROMOTION_REQUEST_COMPLETED" for item in audit)

        repeated = client.post(f"/v1/candidates/{candidate.candidate_id}/promote")
        assert repeated.status_code == 200
        assert repeated.json()["promotion_id"] == body["promotion_id"]
