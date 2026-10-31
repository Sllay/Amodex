# Amodex Persistence and Recovery

Status: **Normative architecture contract — Part 2/3**

This document defines the durable state model, transaction boundaries, lease/recovery behavior, and crash-reconciliation rules that let Amodex continue long-running work after client disconnects, process restarts, worker loss, provider failures, and partial Git operations.

The key principle is simple:

> A task is recoverable only when the system can reconstruct both what should happen next and what may already have happened.

Conversation history alone is not recovery.

---

# 1. Durability goals

Amodex must survive, without corrupting project state:

- Termux disconnect;
- API process restart;
- scheduler/orchestrator restart;
- worker crash;
- runner eviction/termination;
- MCP crash;
- provider timeout/rate limit;
- database connection interruption;
- Git push result lost in transit;
- GitHub API timeout after mutation;
- crash between external side effect and local acknowledgement;
- multiple control-plane replicas racing;
- stale worker continuing after lease loss.

Recovery may pause or require reconciliation, but must not silently duplicate destructive work.

---

# 2. Sources of truth

Different data has different authority.

## 2.1 PostgreSQL

Authoritative for:

- projects and project contracts;
- runs/plans/tasks;
- attempts;
- leases/fencing tokens;
- provider governor state needed for safety;
- capability grants;
- policy decisions;
- checkpoint metadata;
- validation metadata;
- promotion intents/results;
- durable audit events;
- approvals;
- scheduler wait states;
- recovery markers.

## 2.2 Git remote / GitHub

Authoritative for:

- repository objects;
- branch/ref state;
- pushed checkpoint commits;
- PR state;
- target branch history;
- actual merge/ref mutation result.

The database records expected/observed Git state, but does not pretend to replace Git.

## 2.3 Runner filesystem

Non-authoritative except during an active attempt.

It may contain:

- uncommitted edits;
- local commits not yet pushed;
- build caches;
- temporary logs;
- local sockets;
- MCP/editor state;
- generated artifacts awaiting collection.

If something must survive runner loss, it must cross a durability boundary.

## 2.4 Artifact storage

Authoritative for captured evidence payloads while retained.

Metadata/hash remains in PostgreSQL even if a temporary artifact expires.

---

# 3. Database design principles

## 3.1 Current state + event log

Amodex uses normalized current-state tables plus append-only audit events.

Do not use full event sourcing for every field in V1.

A logical transition writes:

```text
BEGIN
  validate expected revision/state
  update current entity
  append audit event
  update related lease/queue rows if required
COMMIT
```

The event must not claim a transition that the current state transaction did not commit.

## 3.2 Revision columns

Mutable authority rows include `revision BIGINT NOT NULL`.

Updates use:

```sql
UPDATE ...
SET ..., revision = revision + 1
WHERE id = :id
  AND revision = :expected_revision;
```

Zero rows updated -> stale conflict.

## 3.3 Timestamps

Store UTC timestamps generated or validated by the control plane/database.

Runner clocks are not trusted for lease expiry decisions.

## 3.4 Soft vs hard delete

Durable execution entities are normally terminalized, not hard-deleted.

Examples:

- cancelled task remains auditable;
- expired lease remains historical;
- superseded plan remains linked;
- revoked capability grant remains visible.

Hard deletion is reserved for retention/privacy policy and owner-authorized maintenance.

---

# 4. Conceptual table groups

The exact SQL DDL belongs to implementation, but table responsibility is frozen.

## 4.1 Project domain

```text
projects
project_contract_versions
project_document_index
project_capabilities
project_component_stability
architecture_decisions
```

## 4.2 Execution domain

```text
runs
plans
plan_tasks
tasks
task_dependencies
worker_attempts
worker_reports
scope_expansion_requests
```

## 4.3 Authority domain

```text
worker_leases
path_leases
capability_grants
tool_authorizations
policy_decisions
approval_requests
```

## 4.4 Provider domain

```text
provider_configs
credential_pool_metadata
provider_health
provider_rate_buckets
provider_circuit_breakers
provider_leases
provider_requests
model_capability_profiles
model_assignments
```

Raw provider credentials are not stored in these tables when a secret store/environment reference can be used.

## 4.5 Repository domain

```text
repository_observations
checkpoints
candidates
promotion_requests
promotion_records
pull_request_links
```

## 4.6 Validation/evidence domain

```text
validator_definitions
validation_runs
validation_evidence
artifacts
environment_fingerprints
error_fingerprints
```

## 4.7 Runtime domain

```text
runner_specs
runner_instances
runner_heartbeats
mcp_server_instances
mcp_probe_results
```

## 4.8 Audit domain

```text
audit_events
incidents
reconciliation_jobs
```

---

# 5. Transactional task claiming

The scheduler must support multiple scheduler/worker processes without double-claiming a task.

Conceptual PostgreSQL pattern:

```sql
BEGIN;
SELECT id
FROM tasks
WHERE status = 'READY'
  AND not_before <= now()
ORDER BY effective_priority DESC, created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;

-- verify DAG/provider/runner/path prerequisites
-- create attempt
-- create worker lease/fencing token
-- change task to LEASED
COMMIT;
```

`SKIP LOCKED` is a claim mechanism, not a substitute for path leases or fencing.

---

# 6. Lease semantics

## 6.1 Worker lease

The lease grants authority to one attempt for a bounded interval.

Fields include:

- lease ID;
- task ID;
- attempt ID;
- runner ID;
- fencing token;
- acquisition time;
- heartbeat interval;
- expiry;
- status.

## 6.2 Heartbeat

Heartbeat updates are lightweight and do not rewrite task payloads.

A heartbeat proves liveness only.

It does not prove:

- tool succeeded;
- code is durable;
- provider request completed;
- checkpoint was pushed.

## 6.3 Expiry grace

Lease expiry is detected using database time.

Do not immediately issue a new writer lease in the same instant if external side effects may still be settling.

Recovery state moves through:

```text
ACTIVE
 -> SUSPECT
 -> EXPIRED
 -> RECONCILING
 -> RECLAIMABLE
```

For low-risk read-only work, policy may shorten this path.

## 6.4 Fencing token

Each reacquisition increments a monotonic token.

Trusted wrappers accept a side-effecting action only if:

```text
request.fencing_token == current_authority_token
```

An old runner regaining network access cannot continue committing/pushing through Amodex wrappers.

---

# 7. Path lease recovery

If writer A dies while owning files:

1. worker lease becomes suspect/expired;
2. path lease stops accepting new mutations from old fencing token;
3. recovery inspects branch/worktree durability;
4. if a remote SAFE checkpoint exists, new attempt starts from it;
5. if only local unpushed work existed and runner is gone, that work is considered lost and reported honestly;
6. new writer lease is issued only after reconciliation.

Amodex must not reconstruct missing code by asking another model to guess what the previous worker probably wrote.

---

# 8. Durable checkpoint boundary

A checkpoint is durable only if required Git objects/refs exist outside the disposable runner.

Default durability classes:

```text
local commit only          -> NOT DURABLE
pushed task branch commit  -> DURABLE
remote bundle/object store -> DURABLE if explicitly supported later
```

`SAFE` checkpoint requires remote durability by default.

A WIP may be local temporarily, but runner shutdown logic checks whether policy requires push first.

---

# 9. Side-effect journal

Every side-effecting tool call that matters to recovery has a durable lifecycle.

```text
PREPARED
 -> DISPATCHED
 -> SUCCEEDED
```

Alternative outcomes:

```text
DISPATCHED -> FAILED_KNOWN
DISPATCHED -> UNKNOWN_OUTCOME
```

Required metadata:

- logical operation ID;
- task/attempt;
- tool/capability;
- idempotency key if available;
- expected external version/SHA if conditional;
- request digest;
- dispatch time;
- result receipt;
- reconciliation handler.

The journal record must be durable before a non-idempotent/destructive call is dispatched.

---

# 10. Idempotency strategy

## 10.1 Read-only

Safe to repeat after timeout.

Examples:

- fetch Git ref;
- read file;
- provider model catalog read;
- status probe.

## 10.2 Idempotent write

Use stable logical key.

Example:

```text
upsert checkpoint metadata where checkpoint_id = X
```

Repeated execution with same payload returns same logical result.

## 10.3 Conditional write

Requires expected external state.

Examples:

- update branch if head SHA equals X;
- update project config if revision equals N;
- promote if target ref equals expected SHA.

Retry only after rereading actual state.

## 10.4 Non-idempotent

Requires reconciliation-specific logic.

Examples may include:

- external notification;
- deployment endpoint with no idempotency key;
- billing/resource creation endpoint that may have accepted request before timeout.

The default for uncertain non-idempotent outcome is `UNKNOWN_OUTCOME`, not blind retry.

---

# 11. Recovery coordinator

A dedicated deterministic recovery loop scans/reconciles entities such as:

- expired worker leases;
- runner lost states;
- `UNKNOWN_OUTCOME` tool calls;
- promotion `RECONCILIATION_REQUIRED`;
- provider requests stuck beyond timeout;
- jobs whose backend says complete while DB says running;
- DB task state inconsistent with remote Git branch/checkpoint.

Recovery does not require a model for ordinary reconciliation.

A model/Director is called only when a semantic decision is necessary.

---

# 12. Process restart recovery

On Control Plane startup:

1. acquire service identity;
2. migrate DB schema if explicitly authorized by deployment process;
3. load active policies;
4. scan expired/suspect leases;
5. reconcile runner backend inventory;
6. reconcile in-flight promotion states;
7. restore provider cooldown/breaker state;
8. schedule reconciliation jobs;
9. resume eligible queues;
10. expose health only after core invariants are loaded.

Do not mark every previously running task `READY` on startup.

---

# 13. Runner loss recovery

## 13.1 Detect

Runner becomes `LOST` when backend/status + heartbeat policy establish it is unavailable.

## 13.2 Freeze authority

- revoke/expire worker lease;
- invalidate active capability grants tied to old fencing token;
- block path writes;
- mark attempt `INTERRUPTED` or `UNKNOWN_SIDE_EFFECTS` depending journal.

## 13.3 Reconcile

Inspect:

- last pushed checkpoint;
- remote task branch head;
- side-effect journal;
- provider requests;
- artifact upload state;
- GitHub PR/ref state.

## 13.4 Resume

If safe:

```text
new runner
 -> checkout remote checkpoint/branch
 -> bootstrap environment
 -> rebuild Context Capsule if dependencies changed
 -> new attempt/new fencing token
 -> continue
```

If unsafe/uncertain:

```text
Task -> RECOVERY_REQUIRED / BLOCKED
```

---

# 14. Client disconnect

Termux/websocket connection is presentation only.

Client disconnect must not:

- cancel run;
- release worker lease;
- stop provider request;
- terminate runner;
- roll back task state.

Explicit cancel is a separate authenticated command.

---

# 15. Cancel semantics

Cancellation is cooperative first, forced second.

Flow:

```text
owner cancel request
 -> Run/Task CANCELLING
 -> stop issuing new provider/tool grants
 -> signal worker interruption
 -> wait bounded grace
 -> collect/push checkpoint if policy and safe
 -> terminate runner if necessary
 -> reconcile side effects
 -> CANCELLED
```

A cancellation cannot erase already-completed Git/provider/external effects.

---

# 16. Provider request recovery

Inference calls are generally externally side-effect-free but can be expensive/quota-consuming and duplicate reasoning.

On timeout/disconnect:

- classify whether transport guarantees no response was completed;
- if unknown, record request as uncertain;
- do not issue parallel identical fallback immediately;
- allow adapter/provider-specific status/reconciliation if available;
- otherwise wait/retry under Governor dedupe policy.

A duplicate model turn must not result in two conflicting tool plans both being executed.

Only the accepted continuation gets tool authority.

---

# 17. MCP process crash

MCP is a local runner dependency.

Recovery policy:

```text
MCP tool call fails
 -> classify server crash vs tool error
 -> stop further calls to instance
 -> preserve tool-call outcome status
 -> restart MCP if task policy allows
 -> capability probe
 -> resume only if workspace/editor state is coherent
```

For Godot, if the editor bridge changed/restarted:

- rerun `godot_status`/equivalent probe;
- verify project identity/path;
- verify LSP/project association;
- do not assume prior runtime scene is still running.

---

# 18. Git crash reconciliation

## 18.1 Commit before push

If runner survives, local commit may be retried for push.

If runner is lost and commit never left runner, it is not recoverable.

## 18.2 Push timeout

Never infer failure from client timeout.

Query remote task branch.

Outcomes:

- remote == intended SHA -> mark success;
- remote == previous expected SHA -> retry conditional push;
- remote == unexpected SHA -> conflict/reconcile;
- remote ref missing -> retry/create according to operation.

## 18.3 Database crash after push

Same reconciliation. Remote Git fact wins for branch existence/head.

The DB records that the intended checkpoint already became durable.

---

# 19. Promotion crash recovery

Promotion receives a durable `promotion_id` and expected target SHA before mutation.

State machine:

```text
REQUESTED
 -> PRECHECK
 -> INTEGRATING
 -> POST_VALIDATING
 -> PROMOTED
```

Exceptional:

```text
INTEGRATING -> UNKNOWN_OUTCOME
UNKNOWN_OUTCOME -> RECONCILIATION_REQUIRED
```

Reconciliation queries:

- PR merge state;
- target branch head;
- merge commit parents;
- source candidate SHA;
- recorded expected target SHA.

Do not issue a second merge until the first outcome is understood.

---

# 20. Database outage behavior

If PostgreSQL is unavailable, authority-bearing operations fail closed.

Workers may finish a currently executing local CPU/read-only operation, but must not continue side-effecting actions that require fresh authorization/lease renewal.

On lease heartbeat failure caused by DB outage:

- worker enters `AUTHORITY_DEGRADED` locally;
- stop new writes after bounded grace;
- preserve local work if possible;
- do not assume lease remains valid indefinitely.

The system must favor temporary pause over split-brain writes.

---

# 21. Multiple Control Plane replicas

If V1 runs one replica, data structures still avoid singleton-memory assumptions.

When multiple replicas exist:

- task claim uses DB locking;
- leader-only maintenance uses advisory lock/lease where appropriate;
- every external mutation uses durable expected-state semantics;
- no correctness depends on process-local scheduler list.

A leader election mechanism may optimize scheduling but is not the only consistency boundary.

---

# 22. Scheduler not-before semantics

Waiting states carry a durable `not_before` timestamp and reason.

Examples:

- provider rate-limit reset;
- circuit breaker cooldown;
- retry backoff;
- runner capacity recheck;
- dependency retry.

Workers are not polled in tight loops.

---

# 23. DAG recovery

Task readiness is derived from durable dependency state.

After restart:

```text
for each nonterminal task:
  evaluate dependencies
  evaluate current task state
  evaluate lease/recovery state
  evaluate policy wait state
  transition only through authorized rules
```

Do not rebuild DAG from model conversation text.

---

# 24. Context freshness after recovery

A resumed attempt does not automatically reuse old Context Capsule.

Recompile if:

- branch/repository SHA changed;
- dependency task promoted new API;
- ADR changed;
- project contract changed;
- scope expanded;
- relevant symbol graph revision changed materially.

If only runner died and repository/context sources are unchanged, reuse by content hash is allowed.

---

# 25. Durable logs vs noisy telemetry

Persist as durable audit:

- state transitions;
- policy decisions;
- high-impact tool calls/results;
- checkpoint/promotion events;
- approvals;
- provider selection/failure classes;
- recovery actions;
- security incidents.

Lower-value telemetry may have bounded retention:

- progress heartbeats;
- debug logs;
- verbose subprocess output;
- raw streaming deltas.

Do not store full prompts repeatedly in event tables.

---

# 26. Retention classes

Conceptual retention:

- `AUDIT_LONG` — decisions, promotions, approvals, incidents;
- `PROJECT_LIFETIME` — task/checkpoint/evidence metadata;
- `RUN_LIFETIME_PLUS` — worker reports, detailed validator logs;
- `SHORT_TELEMETRY` — heartbeats, debug traces;
- `SECRET_NEVER_LOG` — credentials/tokens.

Exact durations are deployment policy, not embedded in business logic.

---

# 27. Backup strategy

Zero-cost mode still needs recoverability.

Minimum production-like personal setup should support:

- PostgreSQL provider backups if free tier offers them, or owner-triggered logical dump;
- periodic logical export of Amodex metadata to encrypted/offsite location when feasible;
- GitHub as durable source history, not DB backup;
- schema migration records;
- restoration test.

A backup never containing provider secrets is preferable when secrets can be reconnected separately.

Part 3 will define concrete deployment/runbook choices after hosting PoC.

---

# 28. Schema migration discipline

Database schema migration rules:

1. migration files are immutable once released;
2. every deployment records applied migration version;
3. forward migration is tested on representative snapshot;
4. destructive migrations require backup + explicit migration plan;
5. rolling compatibility is required if old/new service versions overlap;
6. data contract migrations are separate from DB DDL where appropriate;
7. unknown future entity schema is never silently coerced.

Do not let an agent rewrite migration history to make tests green.

---

# 29. Recovery checkpoints by risk

The scheduler may adapt checkpoint frequency.

Higher risk -> shorter recovery interval:

- dependency upgrade;
- schema migration;
- large multi-file edit;
- long visual test session;
- destructive command;
- expensive bootstrap.

Lower risk -> longer interval:

- documentation read;
- deterministic analysis;
- tiny isolated patch with fast tests.

The goal is not commit spam; it is bounded expected loss.

---

# 30. Recovery objective classes

Instead of promising impossible universal zero-loss recovery, classify operations:

## R0 — reconstructible

Can restart from durable inputs with no meaningful lost work.

## R1 — checkpoint bounded

May lose work since last required checkpoint.

## R2 — reconciliation required

External side effect may have occurred; inspect before continuing.

## R3 — human escalation

System cannot prove a safe automated action.

Every tool/capability gets a recovery class.

---

# 31. Black-Islands recovery benchmark

Amodex must eventually demonstrate on a clone/fork/test fixture of a Black-Islands-like project:

1. start implementation task;
2. modify scoped GDScript;
3. create/push SAFE checkpoint;
4. kill runner process intentionally;
5. let lease expire/reconcile;
6. start fresh runner;
7. recreate environment from project bootstrap;
8. restore exact task branch/checkpoint;
9. reconnect Serena/Godot MCP;
10. rebuild/verify context;
11. continue implementation;
12. run validation;
13. produce candidate without touching unrelated files.

The real Black-Islands repository must not be modified during architecture benchmark design.

---

# 32. Required fault-injection suite

Implementation cannot claim durable recovery until automated tests cover:

- API restart during active task;
- scheduler restart while task READY;
- worker killed before checkpoint;
- worker killed after local commit before push;
- worker killed after push before DB acknowledgement;
- stale worker tries write with old fencing token;
- DB unavailable during heartbeat;
- provider request timeout;
- MCP server killed mid-call;
- runner backend reports lost;
- Git push timeout with successful remote result;
- Git push timeout with no remote result;
- target branch advances during candidate validation;
- crash during promotion;
- crash after merge before post-validation record;
- duplicate recovery coordinator execution;
- two schedulers race to claim same task;
- path lease overlap race;
- schema migration interrupted before commit.

Each test asserts final durable state, not only process exit code.

---

# 33. Recovery observability

User-visible status should say what is happening:

```text
Task C02-C
Status: Recovering
Reason: runner heartbeat expired
Last durable checkpoint: 6e4826a
Remote branch verified: yes
Uncertain side effects: none
Next action: provisioning replacement runner
```

Or:

```text
Promotion blocked
Reason: merge outcome uncertain after API timeout
Action: reconciling GitHub target ref before retry
```

Avoid opaque `agent crashed, retrying...` loops.

---

# 34. Frozen Part 2 decisions

1. PostgreSQL is authoritative for execution state; Git remote is authoritative for repository state.
2. Disposable runner filesystem is not durable truth.
3. Current-state updates and corresponding audit events commit transactionally.
4. Task claim uses database concurrency control; path ownership remains separate.
5. Lease expiry triggers reconciliation before reuse when side effects are possible.
6. Fencing tokens block stale workers after reassignment.
7. Side-effect intent is durable before non-idempotent/destructive dispatch.
8. `UNKNOWN_OUTCOME` is never treated as failed-by-default.
9. External systems are queried during reconciliation rather than guessed.
10. Control Plane restart does not reset every active task to READY.
11. DB outage fails closed for new authority-bearing side effects.
12. Context freshness is reevaluated after meaningful recovery/rebase.
13. SAFE checkpoints are remotely durable by default.
14. Recovery is deterministic where possible; LLM reasoning is used only for semantic replanning.
15. Fault injection is a release gate, not optional QA polish.
