# Git Lifecycle, Checkpoints, Validation, and Promotion

Status: **Architecture baseline — Part 1/3**

This document defines how Amodex uses Git as a recovery, isolation, audit, and promotion system rather than merely a final source-control destination.

## 1. Core rule

Workers never write directly to `main`.

`main` represents promoted state. A worker can produce changes, checkpoints, and candidates, but only the promotion process may move validated work into a protected integration/main lineage.

## 2. Repository roles

Amodex distinguishes these concepts:

- **base SHA** — exact repository state a task was planned against;
- **task branch** — isolated branch owned by a task/workstream;
- **worktree** — filesystem checkout bound to that task branch;
- **checkpoint** — recoverable commit with an explicit trust class;
- **candidate** — validated checkpoint ready for integration against an expected target;
- **promotion** — controlled transition of a candidate into an integration/release/main branch;
- **evidence** — validation results tied to the exact candidate SHA.

These concepts must not be collapsed into “the agent committed something.”

## 3. Branch model

Default conceptual layout:

```text
main
 |
 +-- release/*              optional future release lines
 |
 +-- integration/*          optional project/phase integration lines
       |
       +-- task/<run>/<scope>
       +-- task/<run>/<scope>
```

A project may define its own naming convention. Amodex stores branch policy as project configuration rather than hardcoding one human-readable prefix.

For Black-Islands-like projects, existing project rules such as season branches and `codex/<season>-<phase>-<scope>` can be respected instead of imposing Amodex naming.

## 4. Worktree isolation

Each active write task should use an isolated Git worktree or equivalent checkout boundary.

Benefits:

- concurrent independent tasks do not share uncommitted files;
- path ownership is easier to audit;
- runner recovery has a clear branch/ref target;
- test artifacts cannot accidentally leak into another worker's workspace;
- task cleanup is deterministic.

One worktree does not imply one agent forever. A correction worker can later lease the same task branch after the original writer releases it.

## 5. Expected base SHA

Every implementation task records the exact base SHA from which its branch was created.

Example:

```yaml
repository:
  base_branch: main
  expected_base_sha: 123abc...
  task_branch: task/RUN-42/combat-hitbox
```

This allows Amodex to distinguish:

- task still based on current target;
- target advanced but irrelevant files changed;
- target advanced with relevant dependency changes;
- target history was rewritten;
- candidate cannot be safely promoted without integration/revalidation.

## 6. Checkpoint trust classes

Checkpoint class is explicit metadata, not inferred from commit existence.

### 6.1 `WIP`

Purpose: preserve work from loss.

Requirements:

- repository state is commit-able;
- secrets/generated junk checks pass at minimum;
- commit message identifies task/attempt.

A WIP checkpoint may fail tests and is never promotion eligible.

### 6.2 `SAFE`

Purpose: durable recovery point.

Requirements:

- no parser-breaking corruption within touched scope where a parser exists;
- basic repository integrity checks pass;
- task can be resumed from this commit without relying on unrecorded local state;
- important side-effect state is durable.

SAFE does not imply feature completion.

### 6.3 `VALIDATED`

Purpose: demonstrate the task's scoped acceptance criteria at a specific SHA.

Requirements:

- required validators passed;
- evidence is linked to exact SHA/environment;
- scope check passes;
- no unapproved protected-file edits;
- known limitations are recorded honestly.

### 6.4 `CANDIDATE`

Purpose: mark a validated commit as integration-ready.

Additional requirements:

- target branch relationship checked;
- relevant dependency/context freshness checked;
- merge/rebase strategy chosen;
- required pre-promotion validation complete.

### 6.5 `PROMOTED`

Purpose: record that a candidate entered an approved integration/main lineage.

Promotion metadata records:

- source candidate SHA;
- target pre-promotion SHA;
- target post-promotion SHA;
- method used;
- validators run before/after integration;
- approver/policy decision;
- timestamp.

## 7. Checkpoint triggers

The Orchestrator may require checkpoints:

- after a coherent subtask;
- before long-running validation;
- before destructive/manual testing;
- before dependency or schema migration;
- before broad refactor inside authorized scope;
- before handing work to a correction worker;
- before Runner termination when meaningful unpromoted progress exists;
- when elapsed work/risk exceeds configured recovery threshold.

Checkpoint cadence must balance recoverability against noisy commit history.

## 8. Commit policy

Commits produced by Amodex should be task-scoped and auditable.

Commit metadata should allow linking back to:

- task ID;
- run/attempt;
- checkpoint class;
- validation evidence IDs where relevant.

Do not store raw prompts, provider keys, chain-of-thought, or large transient logs in commit messages.

## 9. Promotion gate

Promotion is a controlled operation.

Conceptual pipeline:

```text
VALIDATED checkpoint
        |
        v
candidate creation
        |
        v
scope + secret + generated-junk check
        |
        v
base/target freshness check
        |
        v
integration simulation / merge
        |
        v
required post-integration validation
        |
     pass? ---- no ---> BLOCKED / correction task
        |
       yes
        v
promote target ref
        |
        v
record PROMOTED metadata
```

A worker cannot self-authorize this sequence.

## 10. Stale base handling

Example:

```text
Task A starts at main SHA 100
Task B promotes -> main SHA 120
Task A finishes at candidate SHA A9
```

Amodex must not blindly move A9 into `main`.

The Promotion Gate compares the candidate's expected base with the actual target.

Possible outcomes:

### 10.1 Target unchanged

Normal promotion path.

### 10.2 Target advanced, no relevant overlap

Rebase/merge/integration may be automated, followed by required validation.

“Files do not overlap” is useful but not sufficient when API contracts changed elsewhere; dependency graph/context freshness must also be considered.

### 10.3 Target advanced with relevant overlap/dependency change

Candidate returns to integration task or becomes blocked pending replanning.

### 10.4 Target history rewritten unexpectedly

Fail closed and escalate. Never silently force-update a protected branch to make histories align.

## 11. Optimistic concurrency for branch updates

Git ref mutation should use expected-head semantics wherever possible.

Conceptually:

```text
update branch X
only if current head == expected SHA
```

If the expected SHA does not match, the update is rejected and the caller re-reads state.

This prevents two orchestration attempts from racing and overwriting each other's branch decisions.

Force pushes to protected branches are forbidden in normal operation.

## 12. Merge strategy

Amodex should support project policy rather than mandate one global merge style.

Potential strategies:

- merge commit — preserves task/checkpoint history visibly;
- squash — cleaner target history, but task/checkpoint audit must remain externally linked;
- rebase + fast-forward — linear history but requires careful stale-base handling.

The chosen project policy must define how checkpoint provenance survives the merge method.

## 13. Pull requests

For GitHub projects, a PR can be the human-visible representation of a Candidate.

The PR body should be generated from structured task/evidence data, including:

- objective;
- scope;
- changed areas;
- acceptance criteria;
- validation summary;
- known limitations;
- ADRs created/superseded;
- candidate SHA;
- target base SHA;
- risks requiring human attention.

A PR description is a view over authoritative task/evidence records, not the only storage of those records.

## 14. GitHub authentication direction

Long-lived production operation should prefer least-privilege GitHub App installation credentials over broad personal access tokens when practical.

Reasons:

- repository-scoped installation permissions;
- short-lived tokens;
- clearer auditability;
- easier permission minimization;
- separation from the Owner's personal account token.

Initial development may use the simplest safe credential available, but the architecture must not assume a permanently stored broad PAT.

Exact GitHub App permission set belongs to Part 2.

## 15. Crash during Git operations

### 15.1 Crash before commit

Uncommitted Runner state may be lost unless a required checkpoint was due. Recovery returns to last durable checkpoint.

### 15.2 Crash after local commit but before push

The commit is not considered durable across disposable Runner loss unless its object/ref was persisted elsewhere. Runner shutdown policy must push required checkpoints before teardown or preserve the Git object store through an approved durable mechanism.

### 15.3 Crash during push

Recovery must query remote branch state before retrying. Never assume the push failed merely because the client did not receive success.

### 15.4 Crash after push but before DB acknowledgement

Recovery reconciles remote ref with intended idempotency/checkpoint ID and records the already-completed result instead of creating a duplicate logical checkpoint.

### 15.5 Crash during promotion

Promotion uses expected-target SHA and records intent/result around the ref/merge operation. Recovery queries GitHub/remote state and reconciles before another promotion attempt.

## 16. Idempotency and Git

Operations should carry logical IDs where useful:

- checkpoint ID;
- candidate ID;
- promotion ID;
- task attempt ID.

The system should be able to answer:

> Did promotion `PROM-123` already occur even if the process crashed before returning success?

This is more important than trying to make every Git command itself magically idempotent.

## 17. Secrets and repository hygiene gate

Before a checkpoint can become VALIDATED/CANDIDATE, run repository hygiene checks appropriate to the project:

- likely secrets/API keys;
- local absolute paths;
- temporary credentials;
- `.env` mistakes;
- generated caches;
- large accidental binaries;
- IDE/editor-local state;
- test output that should be artifacts rather than source;
- dependency archives that should be fetched/pinned instead of committed.

The gate must support project-specific exceptions rather than blindly deleting files.

## 18. Validation evidence binding

Every validation result used for promotion is bound to:

- repository ID;
- commit SHA;
- task/candidate ID;
- validator ID/version;
- relevant environment fingerprint;
- execution timestamp;
- result summary;
- artifact references/hashes.

Evidence from SHA `A` does not prove SHA `B` after a merge.

This is why post-integration validation may be required even when a candidate was already validated in isolation.

## 19. Rollback

Rollback means creating a new controlled repository transition, not erasing history.

Preferred approaches:

- revert promoted commit/merge;
- promote a known-good correction candidate;
- restore service deployment to previous artifact while source correction proceeds.

Do not rewrite shared `main` history to pretend the bad promotion never happened.

## 20. Branch and worktree cleanup

Task branches/worktrees are removed only after their durable state is understood.

Before cleanup:

- required checkpoints pushed;
- candidate/promoted state recorded;
- evidence retained per policy;
- uncommitted files classified/discarded intentionally;
- no active lease remains.

Abandoned task branches can be retained for a bounded audit period and later cleaned by policy.

## 21. Black-Islands compatibility

Amodex must respect a repository's existing Git contract.

Black-Islands currently treats `main` as validated work and uses season/implementation branches with small pushed checkpoints before long validation. Amodex should ingest and enforce that policy rather than replace it with a generic branching fashion.

This project is a benchmark for:

- phase branch handling;
- checkpoint classification;
- stale-base integration;
- evidence-linked commits;
- no direct worker push to `main`;
- recovery after Runner termination.

## 22. Frozen decisions from Part 1

1. `main` is promotion-only in normal agent operation;
2. every task records exact base SHA;
3. isolated write tasks use worktree/branch boundaries;
4. checkpoint trust classes are explicit;
5. a commit is not automatically a validated checkpoint;
6. promotion checks target freshness and revalidates integration where required;
7. ref updates use expected-SHA/lease semantics where possible;
8. Git remote state is queried during crash recovery rather than guessed;
9. evidence is bound to exact code SHA;
10. rollback preserves history;
11. project-specific Git contracts override generic naming conventions;
12. long-lived GitHub auth should move toward least-privilege installation credentials.
