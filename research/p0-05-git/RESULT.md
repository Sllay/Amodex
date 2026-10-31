# P0-05 — Git/GitHub Expected-SHA and Crash Reconciliation

Status: **IN_PROGRESS**

P0-05 tests whether Amodex can make Git/GitHub side effects safely recoverable using exact SHA observations and explicit reconciliation instead of trusting a single client response.

## Environment adaptation

The normative PoC register prefers a separate disposable repository. The connected GitHub surface available during Stage 0 does not expose repository creation, and no pre-existing disposable fixture repository is connected.

Therefore this experiment uses **namespaced disposable branches inside `Sllay/Amodex`**, never `main`, and deletes those refs after the run.

This deviation is part of the evidence rather than being hidden. A separate disposable repository can still be used later when production GitHub App credentials are available.

## Fixture safety boundary

Each workflow run creates branches named from its GitHub Actions run ID:

```text
p0-05-<run-id>-target
p0-05-<run-id>-candidate
p0-05-<run-id>-reconcile
```

The probe contains an explicit path guard that rejects `main` and any ref outside the current fixture prefix before issuing network mutation.

It also records `main` SHA before and after the experiment and requires it to remain unchanged.

## Scenarios

### 1. Exact-base creation

Create target and candidate fixture refs from the exact checked-out source SHA and observe both remote refs.

### 2. Checkpoint push

Create an actual Git commit and push it to the candidate fixture branch. Verify the remote SHA equals the intended checkpoint.

### 3. Ambiguous push acknowledgement

Push another commit successfully, then deliberately discard the local acknowledgement to simulate a client that loses certainty after the remote accepted the write.

Reconcile by querying the remote ref and require it to equal the intended commit SHA.

### 4. Concurrent branch movement / expected-SHA rejection

Move the candidate branch with a separate commit, then attempt a stale force update using:

```text
git push --force-with-lease=<ref>:<expected_sha>
```

The stale update must fail and the concurrently moved remote SHA must remain unchanged.

### 5. PR from exact candidate

Create a real GitHub PR from the candidate fixture branch to the target fixture branch and record exact head/base SHA observations.

### 6. Move target before promotion

Advance the target fixture branch after the PR/base SHA was recorded.

Amodex must detect:

```text
actual_target_sha != expected_base_sha
```

and block stale-base promotion rather than merging anyway.

### 7. Merge acknowledgement loss + reconciliation

Create a fresh reconcile candidate from the new target SHA and a second real GitHub PR.

Submit the merge using the exact expected PR head SHA, then deliberately discard local success acknowledgement.

Reconcile using:

- PR state / `merged_at`;
- target branch SHA.

If the PR is already merged, Amodex must not issue a second promotion attempt.

### 8. Main mutation guard

The path-authority layer must reject `main` locally and the observed remote `main` SHA must remain unchanged for the entire experiment.

This proves the intended path guard, but it does **not** prove that the generic GitHub Actions repository token is technically incapable of writing `main`.

Production credential-level protection still requires branch rules and/or a narrowly scoped GitHub App/token.

## Cleanup

Fixture PRs are closed/merged as required by the scenarios and all disposable fixture refs are deleted in a `finally` block even when a later assertion fails.

## Expected decision shape

If the Git/GitHub reconciliation scenarios pass but credential-level `main` denial cannot be proven with the current generic workflow token, the expected decision is **ADAPT**:

- adopt expected-SHA observation/reconciliation semantics;
- require path guards plus protected-branch rules / scoped GitHub App credentials in production;
- never let a worker token rely only on prompt instructions for branch protection.

## Current decision

**IN_PROGRESS.**

Closure requires a real GitHub Actions run exercising actual remote refs and pull requests without mutating `main`.
