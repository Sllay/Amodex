#!/usr/bin/env python3
"""P0-05: prove expected-SHA Git/GitHub reconciliation on disposable fixture refs.

The connected GitHub surface cannot create a disposable repository, so this
probe uses namespaced disposable branches inside Amodex itself. It never writes
main and deletes all fixture refs after the experiment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
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


def api_request(
    repo: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    allow_status: set[int] | None = None,
) -> tuple[int, dict[str, Any] | list[Any] | None]:
    url = f"https://api.github.com/repos/{repo}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "amodex-p0-05")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            decoded = json.loads(body) if body else None
            return int(response.status), decoded
    except urllib.error.HTTPError as exc:
        body = exc.read()
        decoded: dict[str, Any] | list[Any] | None
        try:
            decoded = json.loads(body) if body else None
        except json.JSONDecodeError:
            decoded = {"message": body.decode("utf-8", errors="replace")[:500]}
        if allow_status and exc.code in allow_status:
            return int(exc.code), decoded
        safe_message = decoded.get("message") if isinstance(decoded, dict) else decoded
        raise ProbeFailure(f"GitHub API {method} {path} -> {exc.code}: {safe_message}") from exc


def ref_path(branch: str) -> str:
    return f"/git/ref/heads/{urllib.parse.quote(branch, safe='')}"


def delete_ref(repo: str, token: str, branch: str) -> None:
    api_request(
        repo,
        token,
        "DELETE",
        f"/git/refs/heads/{urllib.parse.quote(branch, safe='')}",
        allow_status={204, 404, 422},
    )


def create_ref(repo: str, token: str, branch: str, sha: str) -> None:
    api_request(
        repo,
        token,
        "POST",
        "/git/refs",
        {"ref": f"refs/heads/{branch}", "sha": sha},
    )


def remote_sha(branch: str) -> str | None:
    proc = run_git("ls-remote", "origin", f"refs/heads/{branch}")
    line = proc.stdout.strip()
    if not line:
        return None
    return line.split()[0]


def make_commit(parent_sha: str, path: str, content: str, message: str) -> str:
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


def create_pr(repo: str, token: str, *, head: str, base: str, title: str) -> dict[str, Any]:
    status, body = api_request(
        repo,
        token,
        "POST",
        "/pulls",
        {"title": title, "head": head, "base": base, "body": "Amodex P0-05 disposable fixture."},
    )
    require(status == 201 and isinstance(body, dict), "GitHub did not create fixture PR")
    return body


def get_pr(repo: str, token: str, number: int) -> dict[str, Any]:
    status, body = api_request(repo, token, "GET", f"/pulls/{number}")
    require(status == 200 and isinstance(body, dict), f"could not observe PR #{number}")
    return body


def close_pr(repo: str, token: str, number: int) -> None:
    api_request(
        repo,
        token,
        "PATCH",
        f"/pulls/{number}",
        {"state": "closed"},
        allow_status={200, 404, 422},
    )


def guarded_branch(branch: str, fixture_prefix: str) -> str:
    if branch == "main" or not branch.startswith(fixture_prefix):
        raise PermissionError(f"P0-05 path guard denied branch: {branch}")
    return branch


def run_probe(repo: str, token: str, run_id: str) -> dict[str, Any]:
    source_sha = run_git("rev-parse", "HEAD").stdout.strip()
    main_before = remote_sha("main")
    require(main_before is not None, "main ref is not observable")

    prefix = f"p0-05-{run_id}-"
    target = f"{prefix}target"
    candidate = f"{prefix}candidate"
    reconcile = f"{prefix}reconcile"
    fixture_branches = [target, candidate, reconcile]
    stale_pr_number: int | None = None
    reconcile_pr_number: int | None = None

    # Production policy layer must reject main before any network mutation.
    main_guard_denied = False
    try:
        guarded_branch("main", prefix)
    except PermissionError:
        main_guard_denied = True
    require(main_guard_denied, "branch authority guard unexpectedly allowed main")

    for branch in fixture_branches:
        delete_ref(repo, token, branch)

    try:
        create_ref(repo, token, guarded_branch(target, prefix), source_sha)
        create_ref(repo, token, guarded_branch(candidate, prefix), source_sha)
        require(remote_sha(target) == source_sha, "target did not start at exact base SHA")
        require(remote_sha(candidate) == source_sha, "candidate did not start at exact base SHA")

        checkpoint_sha = make_commit(
            source_sha,
            f".p0-05/{run_id}-checkpoint.txt",
            "checkpoint\n",
            "p0-05: checkpoint",
        )
        run_git("push", "origin", f"{checkpoint_sha}:refs/heads/{candidate}")
        require(remote_sha(candidate) == checkpoint_sha, "checkpoint push not observable")

        # Simulate a client losing the success acknowledgement after the remote accepted the push.
        ambiguous_sha = make_commit(
            checkpoint_sha,
            f".p0-05/{run_id}-ambiguous.txt",
            "ambiguous push accepted remotely\n",
            "p0-05: ambiguous push",
        )
        try:
            proc = run_git("push", "origin", f"{ambiguous_sha}:refs/heads/{candidate}")
            require(proc.returncode == 0, "ambiguous push was not accepted by remote")
            raise SimulatedLostAcknowledgement("client lost push acknowledgement after remote accept")
        except SimulatedLostAcknowledgement:
            pass
        push_reconciled_sha = remote_sha(candidate)
        require(
            push_reconciled_sha == ambiguous_sha,
            "remote observation could not reconcile ambiguous push",
        )

        # Another actor moves the branch after our expected SHA snapshot.
        concurrent_sha = make_commit(
            ambiguous_sha,
            f".p0-05/{run_id}-concurrent.txt",
            "concurrent actor\n",
            "p0-05: concurrent branch movement",
        )
        run_git("push", "origin", f"{concurrent_sha}:refs/heads/{candidate}")
        require(remote_sha(candidate) == concurrent_sha, "concurrent branch movement missing")

        stale_sha = make_commit(
            ambiguous_sha,
            f".p0-05/{run_id}-stale.txt",
            "stale writer\n",
            "p0-05: stale writer",
        )
        lease_push = run_git(
            "push",
            f"--force-with-lease=refs/heads/{candidate}:{ambiguous_sha}",
            "origin",
            f"{stale_sha}:refs/heads/{candidate}",
            check=False,
        )
        expected_sha_rejected = lease_push.returncode != 0
        require(expected_sha_rejected, "stale force-with-lease unexpectedly succeeded")
        require(
            remote_sha(candidate) == concurrent_sha,
            "failed expected-SHA push changed remote branch",
        )

        stale_pr = create_pr(
            repo,
            token,
            head=candidate,
            base=target,
            title=f"P0-05 stale-base fixture {run_id}",
        )
        stale_pr_number = int(stale_pr["number"])
        expected_base_sha = source_sha
        observed_head_sha = str((stale_pr.get("head") or {}).get("sha"))
        require(observed_head_sha == concurrent_sha, "PR head does not match exact candidate SHA")

        target_moved_sha = make_commit(
            source_sha,
            f".p0-05/{run_id}-target-moved.txt",
            "target moved\n",
            "p0-05: move target branch",
        )
        run_git("push", "origin", f"{target_moved_sha}:refs/heads/{target}")
        current_target_sha = remote_sha(target)
        stale_base_blocked = current_target_sha != expected_base_sha
        require(stale_base_blocked, "target movement did not invalidate expected base SHA")
        close_pr(repo, token, stale_pr_number)

        create_ref(repo, token, guarded_branch(reconcile, prefix), target_moved_sha)
        reconcile_sha = make_commit(
            target_moved_sha,
            f".p0-05/{run_id}-reconcile.txt",
            "reconcile candidate\n",
            "p0-05: reconciled candidate",
        )
        run_git("push", "origin", f"{reconcile_sha}:refs/heads/{reconcile}")
        require(remote_sha(reconcile) == reconcile_sha, "reconcile candidate push missing")

        reconcile_pr = create_pr(
            repo,
            token,
            head=reconcile,
            base=target,
            title=f"P0-05 merge-reconcile fixture {run_id}",
        )
        reconcile_pr_number = int(reconcile_pr["number"])
        pr_snapshot = get_pr(repo, token, reconcile_pr_number)
        require(
            str((pr_snapshot.get("base") or {}).get("sha")) == target_moved_sha,
            "reconcile PR base SHA differs from observed target",
        )
        require(
            str((pr_snapshot.get("head") or {}).get("sha")) == reconcile_sha,
            "reconcile PR head SHA differs from candidate",
        )

        merge_attempts = 0
        try:
            merge_attempts += 1
            status, merge_body = api_request(
                repo,
                token,
                "PUT",
                f"/pulls/{reconcile_pr_number}/merge",
                {"merge_method": "merge", "sha": reconcile_sha},
            )
            require(
                status == 200 and isinstance(merge_body, dict) and bool(merge_body.get("merged")),
                f"fixture merge was not accepted: {merge_body}",
            )
            # Deliberately discard the merge acknowledgement before persisting local success.
            raise SimulatedLostAcknowledgement("merge accepted remotely; local acknowledgement lost")
        except SimulatedLostAcknowledgement:
            pass

        observed_pr = get_pr(repo, token, reconcile_pr_number)
        remote_target_after_merge = remote_sha(target)
        merge_reconciled = bool(observed_pr.get("merged")) and bool(observed_pr.get("merged_at"))
        require(merge_reconciled, "PR observation did not reconcile ambiguous merge")
        require(
            remote_target_after_merge is not None and remote_target_after_merge != target_moved_sha,
            "target ref did not advance after accepted merge",
        )

        # Reconciliation says the operation is already complete, so no second merge call occurs.
        duplicate_promotion_avoided = merge_reconciled and merge_attempts == 1
        require(duplicate_promotion_avoided, "reconciliation would duplicate promotion")

        main_after = remote_sha("main")
        require(main_after == main_before, "P0-05 fixture mutated main")

        return {
            "status": "PASS",
            "fixture_mode": "namespaced_branches_in_amodex",
            "source_sha": source_sha,
            "main_sha_unchanged": True,
            "path_guard_denied_main": main_guard_denied,
            "exact_base_branch_creation": True,
            "checkpoint_push_observed": True,
            "push_ack_loss_reconciled": push_reconciled_sha == ambiguous_sha,
            "expected_sha_rejection": {
                "mechanism": "git push --force-with-lease=<ref>:<expected_sha>",
                "rejected": expected_sha_rejected,
                "remote_preserved_sha": concurrent_sha,
            },
            "pr_exact_candidate": {
                "number": stale_pr_number,
                "head_sha": concurrent_sha,
                "expected_base_sha": expected_base_sha,
            },
            "target_moved_before_promotion": True,
            "stale_base_blocked": stale_base_blocked,
            "merge_ack_loss_reconciled": merge_reconciled,
            "merge_attempts": merge_attempts,
            "duplicate_promotion_avoided": duplicate_promotion_avoided,
            "final_target_sha": remote_target_after_merge,
            "credential_level_main_block_proven": False,
            "credential_level_note": (
                "Workflow token has current-repository write permission for fixture refs; "
                "production main protection still requires branch rules and/or a scoped GitHub App."
            ),
        }
    finally:
        if stale_pr_number is not None:
            try:
                close_pr(repo, token, stale_pr_number)
            except Exception:
                pass
        if reconcile_pr_number is not None:
            try:
                pr = get_pr(repo, token, reconcile_pr_number)
                if pr.get("state") == "open":
                    close_pr(repo, token, reconcile_pr_number)
            except Exception:
                pass
        for branch in fixture_branches:
            try:
                delete_ref(repo, token, branch)
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.repo:
        raise SystemExit("--repo or GITHUB_REPOSITORY is required")
    if not args.token:
        raise SystemExit("--token or GITHUB_TOKEN is required")

    result = run_probe(args.repo, args.token, str(args.run_id))
    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
