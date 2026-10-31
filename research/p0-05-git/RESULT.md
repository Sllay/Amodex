# P0-05 — Git/GitHub Expected-SHA and Crash Reconciliation

Status: **ADAPT**

P0-05 proved that Amodex can make Git/GitHub side effects recoverable using exact SHA observations, expected-SHA writes, separate worker/promotion authorities, and explicit remote reconciliation instead of trusting one client acknowledgement.

## Environment adaptation

The normative PoC register prefers a separate disposable repository. The connected GitHub surface available during Stage 0 does not expose repository creation, and no pre-existing disposable fixture repository is connected.

The experiment therefore used **namespaced disposable branches inside `Sllay/Amodex`**, never `main`. All `p0-05-*` refs were deleted at the end and a final branch search returned no matches.

This limitation is retained as an explicit adaptation rather than hidden.

## Authority split discovered and adopted

The first implementation attempted to let one GitHub Actions worker identity manage both Git refs and pull requests.

First controller attempt:

```text
workflow run: 41184062614
job: 124487240102
result: failed at POST /pulls
GitHub response: 403 — GitHub Actions is not permitted to create or approve pull requests
```

The repository had granted the workflow token `contents: write` and `pull-requests: write`, but the repository-level GitHub Actions policy still denied PR creation.

We deliberately did **not** weaken that repository setting to make the PoC green.

Instead, the PoC was refactored into the architecture Amodex actually wants:

```text
Worker / GitHub Actions token
  -> checkpoint commits
  -> push fixture refs
  -> expected-SHA / force-with-lease
  -> remote ref reconciliation
  -> NO PR/promotion authority

Control Plane / separate GitHub identity
  -> create PR
  -> compare exact base/head SHA
  -> block stale-base promotion
  -> perform expected-head merge
  -> reconcile ambiguous merge result
```

This separation is now part of the frozen Git architecture.

## Phase A — Worker Git authority

Successful worker run:

```text
workflow: stage0-git
run_id: 41184217540
job_id: 124487680746
Amodex commit: fc947f8174410bc6bff4ea220066d73158d1b4cb
permissions: contents: write
pull-request permission: absent
result: success
```

Observed worker result:

```json
{
  "checkpoint_push_observed": true,
  "exact_base_creation": true,
  "expected_sha_rejection": {
    "actual_remote_sha": "ff8f4090bb464dc8d59c12995503b698e937e795",
    "expected_sha": "1f2f824a2e76a952002899265697ffd76a8a3d38",
    "mechanism": "git push --force-with-lease=<ref>:<expected_sha>",
    "rejected": true
  },
  "main_sha_after": "a5d56260a462bc69021e5af92e098c0fba28f878",
  "main_sha_before": "a5d56260a462bc69021e5af92e098c0fba28f878",
  "main_sha_unchanged": true,
  "path_guard_denied_main": true,
  "pr_authority_exercised_by_worker": false,
  "push_ack_loss_reconciled": true,
  "status": "PASS"
}
```

### Exact-base creation

Both target/candidate worker fixture refs began from the exact checked-out Stage 0 SHA.

### Checkpoint push

A real commit was created and pushed. The remote ref was then read back and required to equal the intended checkpoint SHA.

### Ambiguous push acknowledgement

The remote accepted a push, then the probe deliberately discarded local success acknowledgement.

Recovery was based only on a fresh remote ref observation. The observed remote SHA matched the intended commit, so the operation was marked complete without retrying it blindly.

### Concurrent movement / expected-SHA rejection

A second actor advanced the remote candidate ref. A stale writer then attempted:

```text
git push --force-with-lease=<ref>:<old_expected_sha>
```

The stale operation was rejected and the concurrently advanced ref remained unchanged.

### Worker main guard

The worker path guard explicitly denied `main` before mutation. The remote `main` SHA was recorded before/after and remained:

```text
a5d56260a462bc69021e5af92e098c0fba28f878
```

This proves the Amodex authority/path guard behavior, not credential-level impossibility of a generic repository write token touching `main`.

## Phase B — Control Plane promotion authority

The Control Plane phase used a separate GitHub identity and three disposable refs:

```text
p0-05-controller-20261031-target
p0-05-controller-20261031-candidate
p0-05-controller-20261031-reconcile
```

Initial exact fixture SHA:

```text
fc947f8174410bc6bff4ea220066d73158d1b4cb
```

### Stale-base PR

Candidate commit:

```text
647caa147acf874400a1aed5346846baae051320
```

Real PR:

```text
PR #3
base branch: p0-05-controller-20261031-target
base SHA snapshot: fc947f8174410bc6bff4ea220066d73158d1b4cb
head SHA: 647caa147acf874400a1aed5346846baae051320
```

After the PR snapshot was recorded, the target branch was advanced to:

```text
2059e2a8fafbc7b08b26fba84386a072463b7f44
```

Therefore:

```text
actual_target_sha != expected_base_sha
```

The promotion gate blocked the stale-base PR before any merge attempt. PR #3 was closed unmerged.

### Rebuild from the moved target

A fresh reconcile branch was created directly from:

```text
2059e2a8fafbc7b08b26fba84386a072463b7f44
```

Reconciled candidate commit:

```text
87e82f17b61ca1f6a02cb45ec1e58f11a2d6dd6f
```

Real PR:

```text
PR #4
base SHA: 2059e2a8fafbc7b08b26fba84386a072463b7f44
head SHA: 87e82f17b61ca1f6a02cb45ec1e58f11a2d6dd6f
```

### Expected-head merge and lost acknowledgement reconciliation

Exactly one merge operation was submitted with:

```text
expected_head_sha = 87e82f17b61ca1f6a02cb45ec1e58f11a2d6dd6f
merge_method = merge
```

For the PoC, the immediate success response was treated as lost/ambiguous. No second merge call was made.

A new independent observation then showed:

```text
PR #4 state: closed
merged: true
merged_at: 2026-10-31T08:24:53Z
merge commit: 6fd48b3003e1a23862d318ee0c9781dad70e0cfa
```

A fresh target-branch observation independently showed:

```text
p0-05-controller-20261031-target
SHA: 6fd48b3003e1a23862d318ee0c9781dad70e0cfa
parents:
  - 2059e2a8fafbc7b08b26fba84386a072463b7f44
  - 87e82f17b61ca1f6a02cb45ec1e58f11a2d6dd6f
```

The reconciler therefore concluded that promotion had already completed and issued **zero duplicate merge attempts**.

## Main remained untouched

After the complete controller fixture, a fresh remote read showed `main` still at:

```text
a5d56260a462bc69021e5af92e098c0fba28f878
```

This is the same SHA recorded before the P0-05 fixture.

## Cleanup proof

The Stage 0 Git workflow cleanup was broadened to delete all disposable refs matching:

```text
p0-05-*
```

Cleanup confirmation workflow:

```text
run_id: 41184624504
job_id: 124488890319
worker probe: success
tracked file check: success
cleanup step: success
```

A final GitHub branch search for:

```text
p0-05-
```

returned:

```text
branches: []
```

No disposable P0-05 refs remain.

## Security conclusion

Proven:

- workers can operate on task refs without PR authority;
- Amodex path policy rejects `main`;
- exact expected SHA prevents stale ref overwrites;
- stale target SHA blocks promotion;
- promotion uses exact expected candidate head SHA;
- lost success acknowledgements can be reconciled from remote state;
- reconciliation prevents duplicate promotion attempts;
- `main` remained unchanged throughout the fixture.

Not proven with the current environment:

- that the generic `contents: write` workflow credential is cryptographically/incapably prevented from ever writing `main`;
- the exact same experiment in a physically separate disposable repository.

Production therefore must add platform enforcement, not rely only on application checks:

- protected branch/ruleset for `main`;
- a narrowly scoped GitHub App or equivalent credential split;
- worker credential without promotion/main authority;
- Control Plane credential with only the promotion operations it requires.

## Decision

**ADAPT**.

Accepted for Amodex architecture:

- Git operations are reconciliation-first, not response-trusting;
- every mutation with concurrency risk carries/validates an expected SHA;
- `force-with-lease`-style semantics are required for ref replacement;
- workers own task/checkpoint refs but not PR/promotion authority;
- Control Plane owns PR creation/promotion;
- promotion is blocked when the target SHA differs from the recorded expected base;
- merge uses an exact expected candidate head SHA;
- ambiguous timeout/ack loss is resolved through fresh remote observation before retry;
- if remote state proves the action already happened, the operation becomes complete without duplication;
- `main` receives platform-level protection in addition to Amodex path guards.

Adaptations retained:

- production must use protected-branch/ruleset enforcement and scoped credentials;
- a physically separate disposable repository should be added to CI later if/when the GitHub App can create/manage one, but this is no longer a blocking uncertainty for the reconciliation algorithm.

P0-05 is closed and P0-06 may proceed.

## Next gate

```text
P0-06 — Northflank Control Plane hosting / persistence / reconnect / redeploy
```
