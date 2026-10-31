from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import Authority, Objective, RepositoryAuthority, Scope, TaskPackage
from amodex.git.task_workspace import GitTaskWorkspace
from amodex.tools.scoped_workspace import ScopedWorkspaceTools


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _create_fixture_remote(tmp_path: Path) -> tuple[Path, str]:
    seed = tmp_path / "seed"
    remote = tmp_path / "remote.git"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.name", "Fixture")
    _git(seed, "config", "user.email", "fixture@amodex.invalid")
    (seed / "src").mkdir()
    (seed / "tests").mkdir()
    (seed / "src" / "__init__.py").write_text("", encoding="utf-8")
    (seed / "src" / "calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n\n"
        "def clamp(value: int, low: int, high: int) -> int:\n"
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (seed / "tests" / "test_calculator.py").write_text(
        "from src.calculator import add, clamp\n\n\n"
        "def test_add() -> None:\n"
        "    assert add(2, 3) == 5\n\n\n"
        "def test_clamp() -> None:\n"
        "    assert clamp(-1, 0, 10) == 0\n"
        "    assert clamp(4, 0, 10) == 4\n"
        "    assert clamp(99, 0, 10) == 10\n",
        encoding="utf-8",
    )
    (seed / "README.md").write_text("protected\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "fixture base")
    base_sha = _git(seed, "rev-parse", "HEAD")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "origin", "main")
    return remote, base_sha


def _task(base_sha: str) -> TaskPackage:
    now = datetime.now(timezone.utc)
    return TaskPackage(
        package_id=new_uuid7(),
        task_id=new_uuid7(),
        attempt_id=new_uuid7(),
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
        objective=Objective(statement="Implement clamp and prove it with tests."),
        repository=RepositoryAuthority(
            repository_id="stage2-checkpoint-fixture",
            expected_base_sha=base_sha,
            task_branch=f"work/stage2/{new_uuid7()}",
        ),
        scope=Scope(
            write=("src/calculator.py",),
            read=("src/**", "tests/**"),
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


def test_safe_validated_checkpoint_survives_runner_destruction(tmp_path: Path) -> None:
    remote, base_sha = _create_fixture_remote(tmp_path)
    task = _task(base_sha)
    runner_a = tmp_path / "runner-a" / "repo"
    workspace = GitTaskWorkspace.create(task=task, repository_url=str(remote), path=runner_a)
    tools = ScopedWorkspaceTools(task=task, workspace=runner_a)

    original = tools.read_file("src/calculator.py")
    lease = tools.acquire_write_lease("src/calculator.py", owner="coder")
    tools.patch_file(
        "src/calculator.py",
        owner="coder",
        lease_id=lease.lease_id,
        expected_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
        old_text="    raise NotImplementedError\n",
        new_text="    return max(low, min(value, high))\n",
    )
    tools.release_write_lease(lease_id=lease.lease_id, owner="coder")

    safe, changed = workspace.commit_safe_checkpoint(message="fix: implement clamp")
    assert changed == ("src/calculator.py",)
    assert _git(tmp_path, "--git-dir", str(remote), "rev-parse", "refs/heads/main") == base_sha
    assert _git(
        tmp_path,
        "--git-dir",
        str(remote),
        "rev-parse",
        f"refs/heads/{task.repository.task_branch}",
    ) == safe.commit_sha

    result = tools.run_validator((sys.executable, "-m", "pytest", "-q"), timeout_seconds=30)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = workspace.create_validation_evidence(
        subject_sha=safe.commit_sha,
        validator_id="pytest",
        validator_version="9.0.2",
        stdout=result.stdout,
        stderr=result.stderr,
        passed=True,
    )
    validated = workspace.promote_validated_checkpoint(safe=safe, evidence=evidence)
    report = workspace.make_report(
        changed_files=changed, evidence=evidence, safe=safe, validated=validated
    )
    assert report.status == "completed"
    assert report.validated_checkpoint.commit_sha == safe.commit_sha
    assert report.validated_checkpoint.evidence_ids == (evidence.evidence_id,)

    workspace.destroy()
    assert not runner_a.exists()

    runner_b = tmp_path / "runner-b" / "repo"
    reconstructed = GitTaskWorkspace.reconstruct(
        task=task,
        repository_url=str(remote),
        checkpoint_sha=validated.commit_sha,
        path=runner_b,
    )
    assert reconstructed.current_sha() == validated.commit_sha
    assert reconstructed.status_porcelain() == ()
    rebuilt_tools = ScopedWorkspaceTools(task=task, workspace=runner_b)
    rerun = rebuilt_tools.run_validator((sys.executable, "-m", "pytest", "-q"), timeout_seconds=30)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert "return max(low, min(value, high))" in rebuilt_tools.read_file("src/calculator.py")
    reconstructed.destroy()


def test_failed_attempt_does_not_dirty_remote_or_shared_workspace(tmp_path: Path) -> None:
    remote, base_sha = _create_fixture_remote(tmp_path)
    task = _task(base_sha)
    runner = tmp_path / "failed-runner" / "repo"
    workspace = GitTaskWorkspace.create(task=task, repository_url=str(remote), path=runner)
    tools = ScopedWorkspaceTools(task=task, workspace=runner)

    original = tools.read_file("src/calculator.py")
    lease = tools.acquire_write_lease("src/calculator.py", owner="bad-coder")
    tools.patch_file(
        "src/calculator.py",
        owner="bad-coder",
        lease_id=lease.lease_id,
        expected_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
        old_text="    raise NotImplementedError\n",
        new_text="    return 999\n",
    )
    failing = tools.run_validator((sys.executable, "-m", "pytest", "-q"), timeout_seconds=30)
    assert failing.returncode != 0
    workspace.destroy()
    assert not runner.exists()
    assert _git(tmp_path, "--git-dir", str(remote), "rev-parse", "refs/heads/main") == base_sha
    refs = _git(tmp_path, "--git-dir", str(remote), "for-each-ref", "--format=%(refname)")
    assert task.repository.task_branch not in refs
    assert f"refs/amodex/checkpoints/{task.task_id}" not in refs
