from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import Authority, Objective, RepositoryAuthority, Scope, TaskPackage
from amodex.tools.scoped_workspace import (
    CommandDenied,
    PathLeaseConflict,
    PathPolicyError,
    ScopeViolation,
    ScopedWorkspaceTools,
    SecretBroker,
    SecretDenied,
    ToolPolicyError,
)


def _task_package() -> TaskPackage:
    now = datetime.now(timezone.utc)
    return TaskPackage(
        package_id=new_uuid7(),
        task_id=new_uuid7(),
        attempt_id=new_uuid7(),
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
        objective=Objective(statement="Implement clamp without modifying protected project policy."),
        repository=RepositoryAuthority(
            repository_id="stage2-fixture",
            expected_base_sha="a" * 40,
            task_branch="work/stage2/clamp",
        ),
        scope=Scope(
            write=("src/calculator.py",),
            read=("src/**", "tests/**", "README.md"),
            protected=("README.md",),
            allowed_operations=("read_file", "search_files", "patch_file", "run_tests"),
        ),
        required_capabilities=("coding",),
        authority=Authority(
            may_delegate=False,
            may_request_scope_expansion=True,
            may_commit_checkpoint=True,
            may_push_task_branch=True,
            may_create_pr=False,
            may_promote=False,
        ),
    )


@pytest.fixture()
def fixture_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "src" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "src" / "calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n\n"
        "def clamp(value: int, low: int, high: int) -> int:\n"
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_calculator.py").write_text(
        "from src.calculator import add, clamp\n\n\n"
        "def test_add() -> None:\n"
        "    assert add(2, 3) == 5\n\n\n"
        "def test_clamp() -> None:\n"
        "    assert clamp(-2, 0, 10) == 0\n"
        "    assert clamp(4, 0, 10) == 4\n"
        "    assert clamp(99, 0, 10) == 10\n",
        encoding="utf-8",
    )
    (workspace / "README.md").write_text("PROTECTED PROJECT POLICY\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret\n", encoding="utf-8")
    (workspace / "src" / "outside-link").symlink_to(outside, target_is_directory=True)
    return workspace


def test_scoped_coding_tools_solve_fixture_and_fail_closed(
    fixture_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AMODEX_STAGE2_FORBIDDEN_SECRET", "must-not-leak")
    tools = ScopedWorkspaceTools(
        task=_task_package(),
        workspace=fixture_workspace,
        secrets=SecretBroker(
            values={
                "ALLOWED_TEST_TOKEN": "granted",
                "AMODEX_STAGE2_FORBIDDEN_SECRET": "must-not-leak",
            },
            granted_names=("ALLOWED_TEST_TOKEN",),
        ),
    )

    capsule = tools.build_context_capsule(("src/calculator.py", "tests/test_calculator.py"))
    assert {item.path for item in capsule.files} == {"src/calculator.py", "tests/test_calculator.py"}
    assert "README.md" not in {item.path for item in capsule.files}
    assert tools.search_files(r"NotImplementedError") == (
        ("src/calculator.py", 6, "    raise NotImplementedError"),
    )

    before = tools.run_validator((sys.executable, "-m", "pytest", "-q"), timeout_seconds=30)
    assert before.returncode != 0
    assert "test_clamp" in before.stdout

    original = tools.read_file("src/calculator.py")
    expected_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    lease = tools.acquire_write_lease("src/calculator.py", owner="coder-a")
    with pytest.raises(PathLeaseConflict):
        tools.acquire_write_lease("src/calculator.py", owner="coder-b")

    new_sha = tools.patch_file(
        "src/calculator.py",
        owner="coder-a",
        lease_id=lease.lease_id,
        expected_sha256=expected_sha,
        old_text="    raise NotImplementedError\n",
        new_text="    return max(low, min(value, high))\n",
    )
    assert new_sha != expected_sha
    after = tools.run_validator((sys.executable, "-m", "pytest", "-q"), timeout_seconds=30)
    assert after.returncode == 0, after.stdout + after.stderr

    with pytest.raises(ScopeViolation):
        tools.acquire_write_lease("README.md", owner="coder-a")
    with pytest.raises(PathPolicyError):
        tools.read_file("../outside/secret.txt")
    with pytest.raises(PathPolicyError):
        tools.read_file("src/outside-link/secret.txt")
    with pytest.raises(CommandDenied):
        tools.run_validator(("rm", "-rf", "."))
    with pytest.raises(SecretDenied):
        tools.read_secret("AMODEX_STAGE2_FORBIDDEN_SECRET")
    assert tools.read_secret("ALLOWED_TEST_TOKEN") == "granted"

    tools.release_write_lease(lease_id=lease.lease_id, owner="coder-a")
    correction = tools.acquire_write_lease("src/calculator.py", owner="correction-worker")
    tools.release_write_lease(lease_id=correction.lease_id, owner="correction-worker")

    assert os.environ["AMODEX_STAGE2_FORBIDDEN_SECRET"] == "must-not-leak"
    assert "must-not-leak" not in after.stdout
    assert "must-not-leak" not in after.stderr


def test_patch_requires_fresh_content_and_correct_writer_lease(fixture_workspace: Path) -> None:
    tools = ScopedWorkspaceTools(task=_task_package(), workspace=fixture_workspace)
    original = tools.read_file("src/calculator.py")
    sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    lease = tools.acquire_write_lease("src/calculator.py", owner="coder")

    with pytest.raises(PathLeaseConflict):
        tools.patch_file(
            "src/calculator.py",
            owner="other",
            lease_id=lease.lease_id,
            expected_sha256=sha,
            old_text="raise NotImplementedError",
            new_text="return value",
        )

    (fixture_workspace / "src" / "calculator.py").write_text(original + "\n# concurrent edit\n", encoding="utf-8")
    with pytest.raises(ToolPolicyError, match="stale patch precondition"):
        tools.patch_file(
            "src/calculator.py",
            owner="coder",
            lease_id=lease.lease_id,
            expected_sha256=sha,
            old_text="    raise NotImplementedError\n",
            new_text="    return value\n",
        )
