#!/usr/bin/env python3
"""P0-05 worker phase: prove bounded Git ref mutation and expected-SHA behavior.

Promotion/PR authority is intentionally NOT exercised by this worker token.
That part of the PoC is executed by the separate GitHub control-plane identity.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class ProbeFailure(RuntimeError):
    pass


class SimulatedLostAcknowledgement(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise ProbeFailure(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc


def remote_sha(branch: str) -> str | None:
    proc = run_git("ls-remote", "origin", f"refs/heads/{branch}")
    line = proc.stdout.strip()
    if not line:
        return None
    return line.split()[0]


def make_commit(parent_sha: str, path: str, content: str, message: str) -> str:
    """Create a commit without checking out or dirtying the working tree."""
    with tempfile.TemporaryDirectory(prefix="amodex-p0-05-index-") as temp_dir:
        index_path = str(Path(temp_dir) / "index")
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = index_path

        def git_env(*args: str, input_text: str | None = None) -> str:
            proc = subprocess.run(
                ["git", *args],
                env=env,
                text=True,
                input=input_text,
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                raise ProbeFailure(
                    f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
                )
            return proc.stdout.strip()

        git_env("read-tree", f"{parent_sha}^{{tree}}")
        blob = git_env("hash-object", "-w", "--stdin", input_text=content)
        git_env("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
        tree = git_env("write-tree")
        return git_env("commit-tree", tree, "-p", parent_sha, "-m", message)


def guard_fixture_branch(branch: str, prefix: str) -> str:
    if branch == "main" or not branch.startswith(prefix):
        raise PermissionError(f"P0-05 worker path guard denied branch: {branch}")
    return branch


def run_probe(run_id: str) -> dict[str, Any]:
    source_sha = run_git("rev-parse", "HEAD").stdout.strip()
    main_before = remote_sha("main")
    require(main_before is not None, "main ref is not observable")

    prefix = f"p0-05-worker-{run_id}-"
    target = f"{prefix}target"
    candidate = f"{prefix}candidate"
    fixture_branches = [target, candidate]

    main_guard_denied = False
    try:
        guard_fixture_branch("main", prefix)
    except PermissionError:
        main_guard_denied = True
    require(main_guard_denied, "worker path guard unexpectedly allowed main")

    # Remove leftovers from a prior interrupted attempt with the same run id.
    for branch in fixture_branches:
        run_git("push", "origin", "--delete", branch, check=False)

    result: dict[str, Any] | None = None
    try:
        run_git("push", "origin", f"{source_sha}:refs/heads/{guard_fixture_branch(target, prefix)}")
        run_git("push", "origin", f"{source_sha}:refs/heads/{guard_fixture_branch(candidate, prefix)}")
        require(remote_sha(target) == source_sha, "target did not start at exact source SHA")
        require(remote_sha(candidate) == source_sha, "candidate did not start at exact source SHA")

        checkpoint_sha = make_commit(
            source_sha,
            f".p0-05/{run_id}-checkpoint.txt",
            "checkpoint\n",
            "p0-05: checkpoint",
        )
        run_git("push", "origin", f"{checkpoint_sha}:refs/heads/{candidate}")
        require(remote_sha(candidate) == checkpoint_sha, "checkpoint push not observable")

        ambiguous_sha = make_commit(
            checkpoint_sha,
            f".p0-05/{run_id}-ambiguous.txt",
            "remote accepted, acknowledgement lost\n",
            "p0-05: ambiguous push",
        )
        try:
            accepted = run_git("push", "origin", f"{ambiguous_sha}:refs/heads/{candidate}")
            require(accepted.returncode == 0, "ambiguous push was not remotely accepted")
            # Failure is injected after remote acceptance but before caller records success.
            raise SimulatedLostAcknowledgement("push acknowledgement lost")
        except SimulatedLostAcknowledgement:
            pass

        observed_after_ambiguous = remote_sha(candidate)
        require(
            observed_after_ambiguous == ambiguous_sha,
            "remote observation could not reconcile ambiguous push",
        )

        concurrent_sha = make_commit(
            ambiguous_sha,
            f".p0-05/{run_id}-concurrent.txt",
            "concurrent actor\n",
            "p0-05: concurrent movement",
        )
        run_git("push", "origin", f"{concurrent_sha}:refs/heads/{candidate}")
        require(remote_sha(candidate) == concurrent_sha, "concurrent movement not observable")

        stale_sha = make_commit(
            ambiguous_sha,
            f".p0-05/{run_id}-stale.txt",
            "stale actor\n",
            "p0-05: stale writer",
        )
        stale_push = run_git(
            "push",
            f"--force-with-lease=refs/heads/{candidate}:{ambiguous_sha}",
            "origin",
            f"{stale_sha}:refs/heads/{candidate}",
            check=False,
        )
        expected_sha_rejected = stale_push.returncode != 0
        require(expected_sha_rejected, "stale expected-SHA update unexpectedly succeeded")
        require(
            remote_sha(candidate) == concurrent_sha,
            "failed stale update changed the remote candidate ref",
        )

        main_after = remote_sha("main")
        require(main_after == main_before, "worker fixture mutated main")

        result = {
            "status": "PASS",
            "phase": "worker_git_authority",
            "source_sha": source_sha,
            "fixture_prefix": prefix,
            "exact_base_creation": True,
            "checkpoint_push_observed": True,
            "push_ack_loss_reconciled": True,
            "expected_sha_rejection": {
                "mechanism": "git push --force-with-lease=<ref>:<expected_sha>",
                "expected_sha": ambiguous_sha,
                "actual_remote_sha": concurrent_sha,
                "rejected": True,
            },
            "path_guard_denied_main": True,
            "main_sha_before": main_before,
            "main_sha_after": main_after,
            "main_sha_unchanged": True,
            "pr_authority_exercised_by_worker": False,
        }
    finally:
        for branch in fixture_branches:
            run_git("push", "origin", "--delete", branch, check=False)

    require(result is not None, "worker phase produced no result")
    deleted = {branch: remote_sha(branch) is None for branch in fixture_branches}
    require(all(deleted.values()), f"fixture refs were not fully deleted: {deleted}")
    result["fixture_refs_deleted"] = deleted
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = run_probe(str(args.run_id))
    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
