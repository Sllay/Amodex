# Amodex GitHub Integration

Status: **Normative architecture contract — Part 2/3**

This document defines how Amodex authenticates to GitHub, observes repository state, creates task branches/checkpoints/PRs, reacts to webhooks, and promotes validated work without granting workers broad account authority.

The central rule is:

> GitHub is a durable repository authority. Workers interact with it through narrow, revocable, policy-checked permissions.

---

# 1. Integration goals

Amodex GitHub integration must support:

- project installation/registration;
- repository metadata ingestion;
- branch/ref observation;
- task branch creation;
- scoped push of checkpoints;
- PR creation/update;
- commit/status/CI observation;
- stale-base detection;
- promotion/merge with expected-head protection;
- webhook-driven refresh;
- polling/reconciliation when webhooks are unavailable;
- auditability;
- short-lived least-privilege credentials;
- zero trust in worker-generated Git claims.

---

# 2. Preferred authentication model

## 2.1 GitHub App

Long-lived Amodex deployments should prefer a GitHub App installation model.

Reasons:

- installation scoped to selected repositories/account;
- permission set can be minimized;
- installation tokens are short-lived;
- owner can revoke installation independently of personal credentials;
- clearer audit identity than a personal PAT;
- easier future multi-project separation.

## 2.2 Development fallback

During early development, a fine-grained personal access token may be acceptable if:

- repository scope is minimal;
- permissions are minimal;
- token is stored as secret, not repo config;
- token is revocable;
- architecture does not depend on PAT-only behavior.

Classic broad PAT is not the target production design.

---

# 3. Credential separation

The GitHub App private key/installation-auth secret lives only in Control Plane/secret boundary.

Workers may receive, when required:

- short-lived installation token;
- limited to the relevant repository;
- only permissions needed for their granted Git operation.

A coding worker should not possess organization administration or branch-protection modification rights.

---

# 4. Permission model

Exact permission names must be verified against current GitHub App documentation during implementation, but Amodex separates capability classes conceptually.

## 4.1 Repository read capability

Needed for:

- repository metadata;
- contents/tree/blobs;
- refs/commits;
- PR/issue metadata where context requires;
- statuses/checks observation.

## 4.2 Task branch write capability

Needed for:

- create/update task refs;
- push checkpoint commits;
- never direct protected target mutation by ordinary worker.

## 4.3 Pull request capability

Needed for:

- create/update PR;
- read reviews/comments;
- request reviewers if project policy uses them.

## 4.4 Promotion capability

Held by Control Plane promotion service only.

May require:

- merge PR/update target ref through approved GitHub operation;
- read required checks;
- verify expected head/target state.

## 4.5 Administration capability

Not required for normal Amodex task execution.

Branch protection/ruleset setup is owner/admin installation setup, not worker responsibility.

---

# 5. Repository registration

Project registration records:

```yaml
repository:
  provider: github
  repository_id: "1440484419"
  owner: Sllay
  name: Amodex
  full_name: Sllay/Amodex
  default_branch: main
  installation_id: <opaque>
  visibility: public
  registered_at: ...
```

Use immutable GitHub repository ID for durable identity where available.

Repository rename updates metadata without creating a new Amodex project identity unless owner chooses otherwise.

---

# 6. Initial ingestion

On registration:

1. fetch repository metadata;
2. fetch default branch/ref SHA;
3. index root files/tree under configured limits;
4. discover root contracts (`AGENTS.md` etc.);
5. discover project tooling/MCP declarations without executing them;
6. detect existing branch policy conventions;
7. discover relevant CI workflows/status checks;
8. create initial RepositoryGraph snapshot;
9. store exact observed SHAs/timestamps;
10. report trust decisions still required.

Ingestion is read-only.

---

# 7. Task branch creation

Task branch operation is conditional.

Inputs:

```yaml
repository_id: ...
base_ref: refs/heads/main
expected_base_sha: abc...
task_branch: refs/heads/task/run-42/combat
operation_id: <uuid>
```

Flow:

1. read actual base ref;
2. require actual SHA == expected base SHA unless task policy explicitly replans;
3. create task ref at expected SHA;
4. query created ref to confirm;
5. record branch state in DB.

If task branch already exists:

- if it points to intended SHA and belongs to same operation/task -> reconcile success;
- otherwise conflict; never overwrite blindly.

---

# 8. Checkpoint push

Worker push authority is restricted to its task branch/ref.

Before push:

- active worker/path lease;
- current fencing token;
- branch matches TaskPackage;
- repository hygiene gate appropriate to checkpoint class;
- local commit parent relationship expected;
- no protected target ref.

Push operation uses expected remote head semantics where implementation permits.

After push:

- re-read remote ref;
- bind Checkpoint to remote commit SHA;
- record durable result.

---

# 9. Direct main push prevention

Multiple layers:

1. TaskPackage never grants `main` write;
2. worker Git wrapper denies protected refs;
3. worker token permission/path where possible is minimized;
4. GitHub branch protection/ruleset recommended;
5. promotion service is separate authority;
6. audit incident if worker attempts protected push.

Do not rely solely on prompt: “please don't push main”.

---

# 10. Pull request lifecycle

A Candidate may materialize as a PR.

States mirror but do not replace Amodex candidate state.

PR creation body includes structured projection:

- task/run objective;
- base/candidate SHA;
- changed scope;
- acceptance criteria;
- validation evidence summary;
- known limitations;
- ADR changes;
- risk notes;
- generated-by Amodex marker with durable IDs.

Do not paste chain-of-thought/raw prompts.

---

# 11. PR body ownership

Amodex-managed section may use markers:

```markdown
<!-- amodex:managed:start -->
...generated summary...
<!-- amodex:managed:end -->
```

Human-authored PR notes outside managed section are preserved.

Updating PR should not overwrite owner/reviewer discussion.

---

# 12. Review/comments ingestion

Human review feedback may become:

- owner instruction;
- correction task;
- clarification;
- non-blocking comment.

Amodex must not automatically execute arbitrary text from any PR commenter as owner authority.

Authorization checks commenter identity/role/project policy.

Untrusted external comment is repository/external content subject to prompt-injection rules.

---

# 13. Required checks / CI

Promotion Gate may require GitHub checks/statuses in addition to Amodex validators.

Policy maps named checks to requirements:

```yaml
required_external_checks:
  - name: unit-tests
    conclusion: success
  - name: lint
    conclusion: success
```

Amodex re-reads check state against exact candidate/integration SHA.

A success on previous commit does not transfer.

---

# 14. Promotion via PR merge

Preferred GitHub promotion flow when PR policy is enabled:

```text
Candidate READY
 -> refresh target SHA
 -> ensure PR source SHA exact
 -> ensure required Amodex evidence
 -> ensure required GitHub checks/reviews
 -> merge with expected head SHA
 -> read merge result
 -> read target branch new SHA
 -> run required post-integration validation
 -> PromotionRecord PROMOTED
```

If merge API reports timeout/unknown, enter reconciliation before retry.

---

# 15. Promotion without PR

Some personal/project workflows may permit direct Promotion Gate ref update/merge without PR.

Still requires:

- candidate evidence;
- exact expected target SHA;
- conditional operation;
- post-integration validation;
- durable PromotionRecord.

Worker still never performs it.

---

# 16. Merge strategies

Project policy chooses:

- merge commit;
- squash;
- rebase/fast-forward where supported and safe.

Amodex stores candidate/checkpoint provenance independently so squash does not erase internal audit lineage.

Do not rewrite protected shared history after promotion.

---

# 17. Stale candidate detection

Before promotion compare:

- candidate expected target SHA;
- current target SHA;
- changed files;
- dependency/repository graph changes;
- relevant ADR/spec changes;
- CI/toolchain changes.

Possible outcomes:

```text
FRESH
STALE_NON_OVERLAP_REVALIDATE
STALE_DEPENDENCY_CHANGE
STALE_CONFLICT
TARGET_HISTORY_REWRITTEN
```

No blind merge solely because Git reports no textual conflict.

---

# 18. Webhooks

Recommended events to subscribe only as needed:

- push/ref update;
- pull request lifecycle;
- pull request review/review comment as project policy requires;
- check/status/workflow completion where useful;
- repository rename/archive/install changes where supported.

Do not subscribe to every event category by default.

---

# 19. Webhook verification

For each delivery:

1. verify signature using webhook secret;
2. validate event type/schema;
3. deduplicate delivery ID;
4. store minimal receipt metadata;
5. enqueue reconciliation/refresh;
6. for critical state, re-read GitHub API rather than trusting payload alone.

Webhook is an accelerator/notification, not sole authority.

---

# 20. Polling fallback

If webhook delivery is unavailable/untrusted:

- scheduler performs bounded API refresh for active repos/tasks;
- backoff when GitHub rate-limits;
- only poll active objects frequently;
- completed historical objects refresh rarely/on demand.

Correctness must not require one webhook delivery never being lost.

---

# 21. GitHub API rate limits

GitHub itself is a governed external dependency.

Amodex should:

- cache immutable blobs/commits by SHA;
- avoid refetching unchanged trees;
- use ETags/conditional requests where implementation supports;
- centralize request accounting;
- honor rate-limit reset metadata;
- pause noncritical indexing before critical promotion/status operations.

Never let repository indexing exhaust API capacity needed for active promotion.

---

# 22. Repository graph cache

Git objects are content-addressed.

Cache keys:

- commit SHA;
- tree SHA;
- blob SHA.

If SHA unchanged, parsed symbol/doc index may be reused subject to parser version.

Mutable ref cache always has observation timestamp and is refreshed before authority decisions.

---

# 23. Branch protection/rulesets

Recommended owner setup for `main`/protected integration:

- disallow force push;
- disallow deletion;
- require PR/checks where desired;
- require branch up to date according to chosen policy;
- restrict who/what can push directly;
- keep promotion service permissions explicit.

Exact features vary by GitHub plan/repository settings; Amodex must detect/report what is actually enforced instead of assuming protection exists.

---

# 24. Protection discovery

During project setup, Amodex records:

```yaml
branch_protection_observation:
  target: main
  observed: true/false/unknown
  force_push_protected: ...
  deletion_protected: ...
  required_checks: [...]
  observed_at: ...
```

If GitHub App lacks permission to inspect a setting, state is `unknown`, not fabricated.

Project may still enforce its own Promotion Gate even when GitHub-side protection is absent.

---

# 25. GitHub Actions integration

Amodex may trigger/observe workflows for validation if policy permits.

Rules:

- workflow file is repository code and subject to trust review;
- workflow result bound to exact SHA;
- artifacts downloaded by ID and hashed;
- rerun failed jobs is explicit action;
- Actions cannot grant Amodex evidence for a different commit;
- secrets exposed to Actions remain GitHub-managed/project policy, not copied into Amodex prompts.

---

# 26. Actions as RunnerBackend

When using GitHub Actions as backend:

Control Plane records:

- workflow run ID;
- job IDs;
- triggering SHA/ref;
- runner image labels;
- start/end;
- conclusion;
- artifacts.

Backend adapter translates GitHub run states to RunnerInstance states.

A workflow `success` is backend success, not automatically feature acceptance; validators still interpret evidence.

---

# 27. Git identities

Amodex commits should have clear machine identity, for example GitHub App bot or configured Amodex bot identity.

Commit trailers may include non-sensitive lineage:

```text
Amodex-Run: <uuid>
Amodex-Task: <uuid>
Amodex-Checkpoint: <uuid>
```

Do not put provider model prompts/keys in commit metadata.

---

# 28. Commit signing

Signed commits/tags may be added if zero-cost tooling and GitHub App flow support it cleanly.

Not a V1 correctness prerequisite unless project requires it.

Do not delay core promotion safety waiting for signing.

---

# 29. Forks

Initial V1 focuses on repositories where owner grants installation access.

Fork workflow must explicitly model:

- source repo;
- head repo;
- permission differences;
- PR source branch;
- untrusted external fork content.

Do not assume write access to upstream.

---

# 30. Submodules

Repository ingestion detects submodules.

Policy decides whether to initialize them.

Submodule repository is a separate trust/dependency boundary.

Never follow submodule credentials/URLs blindly during untrusted ingestion.

---

# 31. Git LFS / large files

Detect LFS usage.

Runner bootstrap may need LFS only for tasks requiring those assets.

Avoid downloading giant assets for documentation-only tasks.

LFS bandwidth/storage quotas are resource constraints and belong in scheduling/zero-cost policy.

---

# 32. Private repositories

Private source must remain private across:

- artifacts;
- logs;
- PR comments;
- external providers according to project data policy;
- runner caches.

Making Amodex repository public does not imply connected projects are public.

---

# 33. Installation revocation

If GitHub App installation/repository access is revoked:

- mark project repository access `REVOKED`;
- stop new Git mutations;
- revoke runner Git grants;
- active tasks move to `BLOCKED_REPOSITORY_ACCESS` or safe pause;
- preserve local metadata/evidence;
- do not retry authentication endlessly.

---

# 34. Repository archive/delete/transfer

On webhook/poll observation:

- archive -> project read-only unless owner policy says otherwise;
- delete/not found -> block mutation and retain audit;
- transfer/rename -> reconcile repository immutable ID and installation permissions.

Never create a new repository with same name automatically to “recover”.

---

# 35. Operation idempotency

Every important GitHub mutation carries Amodex operation ID in DB.

Examples:

- create branch;
- create/update PR;
- merge promotion;
- request workflow rerun.

On timeout:

```text
query GitHub current state
 -> matches intended operation? reconcile success
 -> unchanged? retry if safe
 -> unexpected? conflict
```

---

# 36. GitHub error classes

Normalize:

- `GITHUB_AUTH_INVALID`;
- `GITHUB_PERMISSION_DENIED`;
- `GITHUB_REPOSITORY_NOT_FOUND`;
- `GITHUB_REF_NOT_FOUND`;
- `GITHUB_REF_CONFLICT`;
- `GITHUB_EXPECTED_SHA_MISMATCH`;
- `GITHUB_RATE_LIMITED`;
- `GITHUB_VALIDATION_ERROR`;
- `GITHUB_PROTECTED_BRANCH`;
- `GITHUB_CHECKS_PENDING`;
- `GITHUB_MERGE_CONFLICT`;
- `GITHUB_TIMEOUT_UNKNOWN_OUTCOME`;
- `GITHUB_SERVICE_UNAVAILABLE`.

Do not collapse every 422 into the same retry behavior.

---

# 37. Required integration tests

Use a dedicated test repository/fixture, never production repo for destructive cases.

Prove:

1. register repository;
2. read exact default branch SHA;
3. create task branch from expected SHA;
4. push WIP/SAFE checkpoint to task branch;
5. deny worker push to main;
6. create PR from candidate;
7. update managed PR section without deleting human text;
8. observe CI/check status for exact SHA;
9. detect target advancing after candidate creation;
10. reject stale expected-SHA promotion;
11. merge/promotion with expected head;
12. reconcile API timeout after successful merge;
13. webhook signature rejection;
14. webhook replay dedupe;
15. polling catches missed webhook;
16. token revocation blocks further writes cleanly;
17. force push/delete protected target denied;
18. GitHub App token never appears in logs;
19. repo rename preserves repository identity;
20. unknown protection state is reported honestly.

---

# 38. Black-Islands benchmark compatibility

Amodex must ingest existing project Git policy instead of replacing it.

For Black-Islands-style repository:

- `main` validated only;
- season branches where applicable;
- implementation branch naming policy respected;
- checkpoints pushed before long tests;
- no history rewrite;
- validation/evidence linked to commits;
- active phase/context controls scope.

Benchmark should operate on clone/test branch or explicit owner-approved branch, not silently modify production `main`.

---

# 39. GitHub App setup output

When Part 3 defines deployment runbook, setup should produce a machine-readable summary:

```yaml
github_integration:
  app_id: <configured>
  installation_id: <configured>
  repositories:
    - id: ...
      full_name: ...
  permissions_observed:
    contents: read_write
    pull_requests: read_write
    checks: read
    actions: read_or_write_if_backend_used
    administration: none
  webhook:
    configured: true
    signature_verified: true
```

Permission names/levels will be pinned to the current GitHub App API at implementation time.

---

# 40. Frozen Part 2 decisions

1. GitHub App is preferred long-term credential model.
2. Workers never receive the GitHub App private key or broad owner PAT.
3. Repository ID is durable identity; names are metadata.
4. Branch/ref mutations use expected state and reconciliation.
5. Ordinary workers may push only authorized task branches.
6. Promotion authority is separate from worker authority.
7. PRs are projections over Amodex Candidate/Evidence, not sole source of truth.
8. Human comments are authority-checked before becoming instructions.
9. Webhooks are verified/deduped notifications; critical state is re-read.
10. Polling provides correctness fallback for missed webhooks.
11. GitHub checks bind to exact SHA.
12. Branch protection is discovered, not assumed.
13. Actions may serve as RunnerBackend but workflow success is not automatic feature acceptance.
14. GitHub mutation timeout enters reconciliation before retry.
15. Integration tests use disposable/test repository for destructive scenarios.
