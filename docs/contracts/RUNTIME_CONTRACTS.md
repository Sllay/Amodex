# Amodex Runtime Contracts

Status: **Normative architecture contract — Part 2/3**

This document defines the canonical runtime data contracts that all Amodex components must share. It is intentionally implementation-language-neutral. Future Python/Pydantic, SQL, JSON Schema, TypeScript, CLI, API, and worker representations must conform to these semantics rather than inventing parallel shapes.

The goal is to make every important handoff explicit, versioned, auditable, recoverable, and testable.

---

## 1. Contract rules

### 1.1 Canonical wire format

The canonical interchange representation is JSON-compatible data:

- UTF-8 strings;
- booleans;
- signed integers where range is explicitly bounded;
- finite decimals where required;
- arrays;
- objects with string keys;
- RFC 3339 UTC timestamps;
- null only where a field explicitly permits it.

YAML may be used in documentation/configuration, but runtime validation is against the canonical schema semantics.

### 1.2 Identifiers

Runtime entity IDs use UUIDv7 strings unless an external system supplies its own immutable identifier.

Reasons:

- globally unique without central allocation;
- naturally sortable by creation time;
- suitable for distributed workers;
- safer than human-readable IDs as database primary identity.

Human-readable aliases may exist for display, but never replace durable IDs.

Examples:

```text
project_id     = UUIDv7
run_id         = UUIDv7
task_id        = UUIDv7
attempt_id     = UUIDv7
lease_id       = UUIDv7
checkpoint_id  = UUIDv7
evidence_id    = UUIDv7
promotion_id   = UUIDv7
```

### 1.3 Versioning

Every cross-process contract has a `schema_version` integer.

Rules:

- version starts at `1`;
- additive backward-compatible fields may retain the same version only when older readers are specified to ignore unknown fields;
- changing field meaning, requiredness, enum semantics, identity, or invariants requires a new version;
- readers reject unknown future major schema versions rather than guessing;
- migrations are explicit `N -> N+1` transformations;
- persisted records retain their original version until migrated transactionally.

### 1.4 Unknown fields

Internal control-plane APIs default to **reject unknown fields** for authority-bearing objects.

Exceptions may be made for forward-compatible observational payloads, but authority contracts such as `TaskPackage`, `Lease`, `CapabilityGrant`, `PromotionRequest`, and `ToolAuthorization` fail closed on unexpected fields.

### 1.5 Immutable fields

Fields marked immutable may never be changed in place after creation. A correction creates a new version/entity/event.

Examples:

- `task.project_id`;
- `attempt.task_id`;
- `checkpoint.commit_sha`;
- `evidence.subject_sha`;
- `promotion.source_candidate_sha`.

### 1.6 Optimistic concurrency

Mutable authority objects carry a monotonically increasing `revision` integer.

A mutating operation submits `expected_revision`.

```text
read revision 8
modify with expected_revision=8

if current revision == 8:
    commit revision 9
else:
    reject CONFLICT_STALE_REVISION
```

This applies independently from Git expected-SHA checks.

### 1.7 Evidence vs claims

A worker report is a claim.

A validator result is evidence.

A model-generated statement such as `tests_passed: true` is never promoted to `ValidationEvidence` unless the corresponding validator was actually executed and its result captured by the trusted validation path.

---

# 2. Shared primitive types

## 2.1 `EntityRef`

```yaml
schema_version: 1
entity_type: task
entity_id: 018f...
```

Used for cross-links in audit/event records.

## 2.2 `RepositoryRef`

```yaml
schema_version: 1
provider: github
repository_id: "1440484419"
full_name: Sllay/Amodex
clone_url: https://github.com/Sllay/Amodex.git
default_branch: main
```

Invariants:

- `repository_id` is preferred over mutable repository name for durable identity;
- repository rename updates `full_name`, not identity;
- credentials are never embedded.

## 2.3 `GitRefState`

```yaml
schema_version: 1
repository_id: "1440484419"
ref: refs/heads/main
sha: 40-hex-sha
observed_at: 2026-10-31T05:00:00Z
source: github_api
```

A ref observation is not eternal truth. Operations that depend on it must use expected-SHA semantics.

## 2.4 `ArtifactRef`

```yaml
schema_version: 1
artifact_id: <uuidv7>
kind: screenshot
storage_backend: github_actions_artifact
locator: opaque-backend-reference
sha256: <64-hex>
media_type: image/png
size_bytes: 183204
retention_class: validation
created_at: 2026-10-31T05:00:00Z
```

Rules:

- `locator` must not contain credentials;
- integrity hash is required for promotion-relevant artifacts;
- artifacts may expire, but evidence metadata must retain the hash and provenance.

## 2.5 `EnvironmentFingerprint`

```yaml
schema_version: 1
runner_image_digest: sha256:...
os:
  family: linux
  arch: amd64
toolchain:
  python: "3.13.1"
  node: "22.23.4"
  git: "2.47.0"
project_tools:
  godot: "4.7.2"
  godot_mcp: "0.1.2"
  serena: "2.1.0"
lockfiles:
  - path: uv.lock
    sha256: ...
```

The fingerprint contains facts needed to judge reproducibility, not every installed OS package.

---

# 3. Project contract

## 3.1 `ProjectContract`

Represents a repository's Amodex-facing execution contract after ingestion.

```yaml
schema_version: 1
project_id: <uuidv7>
revision: 4
repository:
  provider: github
  repository_id: "..."
  full_name: Sllay/Black-Islands
  default_branch: main
runtime_roots:
  - game/
document_authority:
  root_contracts:
    - AGENTS.md
  goals:
    - GOALS.md
    - PLANS.md
  decisions_glob: docs/decisions/*.md
  focused_specs_globs:
    - docs/architecture/*.md
    - docs/design/*.md
git_policy:
  worker_direct_main_write: false
  branch_strategy: project_defined
  promotion_target: main
cost_policy_id: zero_cost_default
security_profile_id: default_untrusted_repo
capability_sources:
  - project_mcp
  - language_servers
  - builtin_tools
created_at: ...
updated_at: ...
```

Invariants:

- ingestion never silently grants execution capability;
- repository documents can propose project policy but cannot override owner/system security or cost policy;
- contradictory project documents are recorded as an ingestion conflict, not arbitrarily resolved by filename order unless an explicit authority rule exists.

---

# 4. Run and planning contracts

## 4.1 `Run`

A `Run` is the durable execution of one owner-level objective.

```yaml
schema_version: 1
run_id: <uuidv7>
project_id: <uuidv7>
revision: 1
status: PLANNING
objective:
  title: Implement inventory interaction foundation
  source: owner_request
  source_ref: null
created_by:
  actor_type: owner
  actor_id: Sllay
technical_design_ref:
  artifact_id: null
cost_policy_snapshot:
  policy_id: zero_cost_default
  paid_inference_allowed: false
  paid_compute_allowed: false
base_repository_state:
  target_ref: refs/heads/main
  target_sha: ...
created_at: ...
updated_at: ...
```

Allowed run states:

```text
DRAFT
PLANNING
ACTIVE
PAUSED
BLOCKED
VALIDATING
COMPLETED
FAILED
CANCELLED
```

A `Run` does not become `COMPLETED` merely because all workers stopped. Completion requires the run's completion policy.

## 4.2 `Plan`

The Director proposes a plan. The Orchestrator validates and accepts a revision.

```yaml
schema_version: 1
plan_id: <uuidv7>
run_id: <uuidv7>
revision: 3
status: ACCEPTED
created_by:
  role: technical_director
  model_execution_id: <uuidv7>
objective_summary: ...
tasks:
  - <task_id>
assumptions:
  - id: A1
    text: Godot MCP is available in project toolchain
    verification: capability_probe
risks:
  - id: R1
    severity: medium
    description: visual runner memory may exceed free compute
    mitigation: POC-INFRA-001
architecture_change_requested: false
accepted_at: ...
```

Only an `ACCEPTED` plan revision can release tasks to `READY`.

---

# 5. Task contracts

## 5.1 `Task`

A durable DAG node.

```yaml
schema_version: 1
task_id: <uuidv7>
run_id: <uuidv7>
revision: 7
key: C02-C-crafting
kind: implementation
status: READY
priority: 50
depends_on:
  - <task_id>
blocking_policy: all_success
objective: Implement five-slot crafting queue using accepted inventory transaction authority
acceptance_contract_id: <uuidv7>
context_policy_id: default_scoped
created_at: ...
updated_at: ...
```

Task kinds initially recognized:

- `analysis`;
- `research`;
- `architecture`;
- `implementation`;
- `correction`;
- `review`;
- `integration`;
- `migration`;
- `validation`;
- `benchmark`.

## 5.2 Task states

Normative lifecycle:

```text
DRAFT
  -> PLANNED
  -> READY
  -> LEASED
  -> EXECUTING
  -> VALIDATING
  -> CHECKPOINTED
  -> CANDIDATE
  -> PROMOTED
```

Additional states:

- `WAITING_DEPENDENCY`;
- `WAITING_PROVIDER`;
- `WAITING_RUNNER`;
- `PAUSED`;
- `CORRECTION_REQUIRED`;
- `RECOVERY_REQUIRED`;
- `BLOCKED`;
- `FAILED`;
- `CANCELLED`;
- `REJECTED`.

Important distinction:

- provider quota exhaustion normally moves work to `WAITING_PROVIDER`, not `FAILED`;
- free runner exhaustion normally moves work to `WAITING_RUNNER`;
- repeated equivalent defects may move task to `BLOCKED` pending Director strategy.

## 5.3 `TaskPackage`

This is the bounded authority packet delivered to one worker attempt.

```yaml
schema_version: 1
package_id: <uuidv7>
task_id: <uuidv7>
attempt_id: <uuidv7>
package_revision: 1
issued_at: ...
expires_at: ...
objective:
  statement: Implement melee hit registration
  non_goals:
    - redesign combat stats
    - change save schema
acceptance:
  criteria:
    - id: AC1
      statement: one target receives at most one hit per swing
      validator_refs:
        - combat_melee_focused
    - id: AC2
      statement: changed GDScript has no diagnostics
      validator_refs:
        - gdscript_lsp_changed
repository:
  repository_id: "..."
  expected_base_sha: ...
  task_branch: task/run-42/combat-hitbox
  checkpoint_ref: null
scope:
  write:
    - game/scripts/combat/melee.gd
    - game/scripts/combat/hitbox.gd
  read:
    - game/scripts/player/**
    - game/scripts/stats/**
  protected:
    - game/scripts/save/**
    - game/scripts/world/**
  allowed_operations:
    - read
    - patch
    - create_within_write_scope
capabilities:
  required:
    - filesystem.patch
    - git.task_branch
    - gdscript.symbol_navigation
  optional:
    - godot.runtime.smoke
context_capsule_id: <uuidv7>
decision_refs:
  - ADR-0012
stability_constraints:
  - path: game/scripts/stats/**
    state: STABLE
    modification_allowed: false
resource_limits:
  max_wall_seconds: 3600
  max_provider_requests: 120
  max_tool_calls: 500
  max_parallel_tool_calls: 4
authority:
  may_delegate: false
  may_request_scope_expansion: true
  may_commit_checkpoint: true
  may_push_task_branch: true
  may_create_pr: false
  may_promote: false
```

### 5.3.1 TaskPackage invariants

The worker cannot widen these fields locally:

- write scope;
- protected scope;
- capabilities;
- cost permission;
- delegation permission;
- promotion authority;
- repository identity.

A scope expansion is a request to the Orchestrator/Director and yields a new package revision if approved.

### 5.3.2 Scope matching

Path matching must operate on canonical repository-relative paths after resolving `.`/`..`, symlinks, case behavior, and repository root.

A path that canonicalizes outside the task worktree is always denied.

---

# 6. Context contracts

## 6.1 `ContextCapsule`

A content-addressed package of task-relevant context.

```yaml
schema_version: 1
context_capsule_id: <uuidv7>
project_id: <uuidv7>
task_id: <uuidv7>
revision: 1
repository_sha: ...
generated_at: ...
compiler_version: amodex-context-v1
budget:
  target_tokens: 18000
  hard_max_tokens: 24000
sections:
  - type: project_rule
    source:
      path: AGENTS.md
      sha256: ...
      start_line: 1
      end_line: 120
    authority: root_contract
  - type: focused_spec
    source:
      path: docs/design/PLAYER_COMBAT.md
      sha256: ...
      start_line: 40
      end_line: 160
    authority: focused_spec
  - type: symbol
    source:
      path: game/scripts/combat/melee.gd
      git_blob_sha: ...
      symbol: MeleeController.attack
  - type: decision
    decision_id: ADR-0012
excluded_reasons:
  - source: docs/GDD.md
    reason: narrow task; focused spec supersedes broad reread
freshness:
  dependency_graph_revision: 41
  repository_sha: ...
```

Rules:

- capsule is immutable after issue;
- any content change produces a new capsule revision/ID;
- workers receive source provenance, not unattributed pasted prose;
- untrusted repository text is labeled as repository content, not merged into system authority.

---

# 7. Worker execution contracts

## 7.1 `WorkerAttempt`

```yaml
schema_version: 1
attempt_id: <uuidv7>
task_id: <uuidv7>
sequence: 2
revision: 1
status: RUNNING
worker_profile: coding
model_assignment_id: <uuidv7>
runner_id: <uuidv7>
lease_id: <uuidv7>
started_at: ...
ended_at: null
recovery_from_attempt_id: <uuidv7-or-null>
```

Attempt state is distinct from Task state. A task can survive multiple attempts.

## 7.2 `WorkerReport`

```yaml
schema_version: 1
report_id: <uuidv7>
task_id: <uuidv7>
attempt_id: <uuidv7>
status: COMPLETED
summary: Implemented hit registration using existing attack window authority
files:
  changed:
    - game/scripts/combat/melee.gd
    - game/scripts/combat/hitbox.gd
  created: []
  deleted: []
  read:
    - game/scripts/player/player.gd
claims:
  - kind: behavior
    statement: one-hit-per-swing guard is implemented
requested_validators:
  - combat_melee_focused
  - gdscript_lsp_changed
risks: []
decisions_requested: []
scope_deviation:
  occurred: false
  details: null
checkpoint_request:
  requested_class: SAFE
```

The report may not assert trusted pass/fail evidence.

## 7.3 `ScopeExpansionRequest`

```yaml
schema_version: 1
request_id: <uuidv7>
task_id: <uuidv7>
attempt_id: <uuidv7>
requested_write_paths:
  - game/scripts/combat/combat_state.gd
reason_category: NEW_REQUIREMENT
reason: Existing state owner must expose swing instance ID to satisfy AC1
impact:
  stability_state: ACTIVE
  other_tasks_affected: []
status: PENDING
```

A worker must stop write attempts against the requested path until approved.

---

# 8. Lease and ownership contracts

## 8.1 `WorkerLease`

```yaml
schema_version: 1
lease_id: <uuidv7>
task_id: <uuidv7>
attempt_id: <uuidv7>
runner_id: <uuidv7>
fencing_token: 119
status: ACTIVE
acquired_at: ...
heartbeat_interval_seconds: 15
expires_at: ...
```

The fencing token is monotonic per leaseable authority domain.

## 8.2 `PathLease`

```yaml
schema_version: 1
path_lease_id: <uuidv7>
project_id: <uuidv7>
task_id: <uuidv7>
attempt_id: <uuidv7>
mode: WRITE
patterns:
  - game/scripts/combat/melee.gd
  - game/scripts/combat/hitbox.gd
fencing_token: 311
status: ACTIVE
acquired_at: ...
expires_at: ...
```

Invariants:

- overlapping WRITE leases are rejected;
- READ leases may coexist unless a project policy defines stronger isolation;
- lease expiration removes authority but does not automatically classify side effects as safe to rerun;
- stale fencing token writes are rejected by trusted tool wrappers.

---

# 9. Capability and authorization contracts

## 9.1 `CapabilityDescriptor`

```yaml
schema_version: 1
capability_id: godot.runtime.screenshot
provider_type: mcp
risk_class: medium
side_effect_class: read_runtime_state
requires:
  - godot.editor_bridge
supported_inputs:
  - viewport
  - window
output_kinds:
  - image/png
```

## 9.2 `CapabilityGrant`

```yaml
schema_version: 1
grant_id: <uuidv7>
attempt_id: <uuidv7>
capability_id: godot.runtime.screenshot
scope:
  project_id: <uuidv7>
  workspace_id: <uuidv7>
constraints:
  max_calls: 20
  network_exposure: none
issued_at: ...
expires_at: ...
fencing_token: 119
```

A discovered MCP tool is not usable until mapped to an approved capability or explicitly granted as an opaque tool under a stricter policy.

## 9.3 `ToolAuthorization`

Created for high-impact tool calls before execution.

```yaml
schema_version: 1
authorization_id: <uuidv7>
attempt_id: <uuidv7>
tool_call_id: <uuidv7>
capability_id: git.push_task_branch
side_effect_class: conditional_write
idempotency_key: checkpoint-<uuid>
expected:
  branch: task/run-42/combat-hitbox
  head_sha: abc...
allowed: true
policy_reasons:
  - task branch push permitted
  - fencing token current
  - zero cost
issued_at: ...
expires_at: ...
```

---

# 10. Tool-call contracts

## 10.1 `ToolCall`

```yaml
schema_version: 1
tool_call_id: <uuidv7>
task_id: <uuidv7>
attempt_id: <uuidv7>
sequence: 44
capability_id: filesystem.patch
tool_implementation_id: builtin.patch.v1
arguments_digest: sha256:...
redacted_arguments:
  path: game/scripts/combat/melee.gd
  patch_sha256: ...
side_effect_class: CONDITIONAL_WRITE
idempotency_key: null
authorization_id: <uuidv7>
started_at: ...
```

Raw sensitive arguments may exist only in the execution boundary when needed and must not be blindly copied into audit logs.

## 10.2 Side-effect classes

Canonical enum:

- `READ_ONLY`;
- `IDEMPOTENT_WRITE`;
- `CONDITIONAL_WRITE`;
- `NON_IDEMPOTENT`;
- `DESTRUCTIVE`.

Examples:

| Operation | Class |
|---|---|
| read file | READ_ONLY |
| PUT deterministic checkpoint metadata | IDEMPOTENT_WRITE |
| update Git ref with expected SHA | CONDITIONAL_WRITE |
| start external deployment without idempotency support | NON_IDEMPOTENT |
| delete remote branch/history | DESTRUCTIVE |

## 10.3 `ToolResult`

```yaml
schema_version: 1
tool_result_id: <uuidv7>
tool_call_id: <uuidv7>
status: SUCCEEDED
started_at: ...
completed_at: ...
exit_code: 0
result_digest: sha256:...
summary: Patched symbol successfully
artifact_refs: []
side_effect_receipt:
  type: git_worktree_change
  durable: false
error: null
```

Failure status enum:

- `SUCCEEDED`;
- `FAILED_RETRYABLE`;
- `FAILED_NON_RETRYABLE`;
- `DENIED_POLICY`;
- `CANCELLED`;
- `UNKNOWN_OUTCOME`.

`UNKNOWN_OUTCOME` is mandatory for cases where the process lost contact after sending a potentially side-effecting request and cannot prove whether it completed.

---

# 11. Provider contracts

## 11.1 `ModelCapabilityProfile`

```yaml
schema_version: 1
profile_id: <uuidv7>
provider_id: google
model_id: gemini-example
observed_at: ...
source: benchmark_plus_declared
capabilities:
  coding: 0.87
  debugging: 0.81
  planning: 0.90
  research: 0.94
  vision: 0.96
  tool_use: 0.88
  long_context: 0.91
features:
  native_tool_calling: true
  structured_output: true
  vision_input: true
  parallel_tool_calls: true
context_window_tokens: 1000000
cost_class: FREE_ELIGIBLE
confidence: 0.72
```

Scores are normalized observations, not permanent truths.

## 11.2 `ModelRequirement`

```yaml
schema_version: 1
requirement_id: <uuidv7>
role: correction
minimum:
  debugging: 0.75
  tool_use: 0.70
required_features:
  - native_tool_calling
  - structured_output
preferred:
  coding: 0.80
max_cost_class: FREE_ELIGIBLE
```

## 11.3 `ModelAssignment`

```yaml
schema_version: 1
model_assignment_id: <uuidv7>
attempt_id: <uuidv7>
provider_id: groq
model_id: ...
credential_pool_id: free-primary
selection_score: 0.842
selection_reasons:
  - satisfies structured output requirement
  - provider has free capacity
  - lower expected latency than next eligible candidate
fallback_chain:
  - provider_id: nvidia
    model_id: ...
assigned_at: ...
```

The fallback chain is eligibility metadata, not permission to switch blindly. The Governor rechecks capability and policy at failure time.

---

# 12. Provider lease and request contracts

## 12.1 `ProviderLease`

```yaml
schema_version: 1
provider_lease_id: <uuidv7>
provider_id: google
credential_pool_id: free-primary
model_id: ...
attempt_id: <uuidv7>
reserved:
  requests: 1
  estimated_input_tokens: 18000
  estimated_output_tokens: 6000
concurrency_slot: 1
issued_at: ...
expires_at: ...
```

Provider calls without a valid lease are denied in governed mode.

## 12.2 `ProviderRequestRecord`

```yaml
schema_version: 1
provider_request_id: <uuidv7>
attempt_id: <uuidv7>
provider_id: google
model_id: ...
provider_lease_id: <uuidv7>
request_class: agent_turn
started_at: ...
completed_at: ...
http_status: 200
usage:
  input_tokens: 17321
  output_tokens: 2114
  cached_input_tokens: 0
cost:
  currency: USD
  amount: "0.000000"
  billing_class: free
result: SUCCESS
retry_after_seconds: null
error_class: null
```

If provider does not report usage, fields are marked `unknown`; they are never fabricated.

---

# 13. Checkpoint contracts

## 13.1 `Checkpoint`

```yaml
schema_version: 1
checkpoint_id: <uuidv7>
task_id: <uuidv7>
attempt_id: <uuidv7>
class: SAFE
repository_id: "..."
branch: task/run-42/combat-hitbox
commit_sha: ...
parent_sha: ...
base_sha: ...
created_at: ...
remote_durable: true
validation_evidence_ids: []
repository_hygiene:
  secrets_scan: PASS
  generated_junk: PASS
  path_scope: PASS
known_failures:
  - validator_id: combat_melee_focused
    status: NOT_RUN
```

Classes:

- `WIP`;
- `SAFE`;
- `VALIDATED`;
- `CANDIDATE`;
- `PROMOTED` is represented by `Promotion`, not merely by changing checkpoint class in place.

Checkpoint identity is immutable. If additional validation later elevates trust, create a trust assertion/candidate referring to the same commit rather than mutating historical evidence.

## 13.2 `Candidate`

```yaml
schema_version: 1
candidate_id: <uuidv7>
task_id: <uuidv7>
checkpoint_id: <uuidv7>
source_sha: ...
expected_target_ref: refs/heads/main
expected_target_sha: ...
evidence_ids:
  - <uuidv7>
status: READY
created_at: ...
```

Candidate state:

- `PREPARING`;
- `READY`;
- `STALE`;
- `BLOCKED`;
- `PROMOTING`;
- `PROMOTED`;
- `REJECTED`.

---

# 14. Validation contracts

## 14.1 `ValidatorDefinition`

```yaml
schema_version: 1
validator_id: gdscript_lsp_changed
validator_version: 3
kind: diagnostic
trust_class: deterministic_tool
required_capabilities:
  - gdscript.lsp
subject: changed_files
pass_condition:
  errors: 0
  warnings: project_policy
artifact_policy:
  retain_logs: on_failure
```

A validator definition is versioned. Evidence from version 2 is not silently treated as version 3.

## 14.2 `ValidationEvidence`

```yaml
schema_version: 1
evidence_id: <uuidv7>
validator_id: gdscript_lsp_changed
validator_version: 3
task_id: <uuidv7>
attempt_id: <uuidv7>
subject:
  repository_id: "..."
  commit_sha: ...
result: PASS
started_at: ...
completed_at: ...
environment_fingerprint_id: <uuidv7>
metrics:
  errors: 0
  warnings: 0
artifacts:
  - <artifact_id>
provenance:
  runner_id: <uuidv7>
  tool_result_ids:
    - <uuidv7>
limitations:
  - physical mobile device not exercised
```

Evidence result enum:

- `PASS`;
- `FAIL`;
- `ERROR`;
- `INCONCLUSIVE`;
- `SKIPPED_POLICY`.

`ERROR` means validator execution failed; it is not equivalent to task failure and is never equivalent to PASS.

## 14.3 `ErrorFingerprint`

```yaml
schema_version: 1
fingerprint_id: sha256:...
validator_id: combat_melee_focused
normalized:
  category: assertion_failure
  assertion: one_hit_per_swing
  path: game/scripts/combat/hitbox.gd
  symbol: Hitbox.register_target
  stable_message_tokens:
    - expected
    - one
    - got
    - two
occurrences: 3
first_seen_attempt: <uuidv7>
last_seen_attempt: <uuidv7>
```

Dynamic values such as timestamps, absolute temp paths, random ports, and memory addresses are removed before hashing.

---

# 15. Promotion contracts

## 15.1 `PromotionRequest`

```yaml
schema_version: 1
promotion_id: <uuidv7>
candidate_id: <uuidv7>
repository_id: "..."
source_sha: ...
target_ref: refs/heads/main
expected_target_sha: ...
method: merge
required_pre_evidence:
  - <uuidv7>
required_post_validators:
  - main_smoke
created_by:
  actor_type: orchestrator
created_at: ...
```

## 15.2 `PromotionRecord`

```yaml
schema_version: 1
promotion_id: <uuidv7>
status: PROMOTED
source_sha: ...
target_ref: refs/heads/main
target_sha_before: ...
target_sha_after: ...
method: merge
integration_sha: ...
pre_evidence_ids:
  - ...
post_evidence_ids:
  - ...
policy_decision_id: <uuidv7>
approval:
  mode: policy
  actor: amodex_promotion_gate
started_at: ...
completed_at: ...
```

Possible statuses:

- `REQUESTED`;
- `PRECHECK`;
- `BLOCKED_STALE`;
- `BLOCKED_POLICY`;
- `INTEGRATING`;
- `POST_VALIDATING`;
- `PROMOTED`;
- `FAILED`;
- `UNKNOWN_OUTCOME`;
- `RECONCILIATION_REQUIRED`.

---

# 16. Architecture decision contracts

## 16.1 `DecisionRecord`

Amodex may parse Markdown ADRs, but internally it tracks structured metadata.

```yaml
schema_version: 1
decision_id: ADR-0012
project_id: <uuidv7>
title: Inventory mutations use one transaction coordinator
status: ACCEPTED
scope:
  - game/scripts/inventory/**
  - game/scripts/crafting/**
source:
  path: docs/decisions/ADR-0012-inventory-transactions.md
  git_blob_sha: ...
supersedes: []
superseded_by: null
rationale_digest: sha256:...
accepted_at: ...
```

Decision status:

- `PROPOSED`;
- `ACCEPTED`;
- `SUPERSEDED`;
- `REJECTED`;
- `DEPRECATED`.

A later worker cannot reinterpret `SUPERSEDED` as current because the old file still exists.

---

# 17. Runner contracts

## 17.1 `RunnerSpec`

```yaml
schema_version: 1
runner_spec_id: <uuidv7>
profile: godot_visual
backend_preference:
  - northflank
  - github_actions
  - local_container
resources:
  min_cpu_millis: 500
  min_memory_mb: 1536
  max_wall_seconds: 3600
network:
  outbound: restricted
  inbound_public: false
workspace:
  repository_id: "..."
  ref: task/run-42/ui
  expected_sha: ...
capabilities:
  - filesystem.patch
  - git.task_branch
  - godot.editor_bridge
  - godot.runtime.screenshot
toolchain_manifest_ref: project
environment:
  display: virtual
  gpu: optional
artifacts:
  capture:
    - logs
    - screenshots
```

Resource values are scheduler requests, not promises that a free provider can satisfy them.

## 17.2 `RunnerInstance`

```yaml
schema_version: 1
runner_id: <uuidv7>
backend: northflank
backend_instance_id: opaque
spec_id: <uuidv7>
status: READY
created_at: ...
started_at: ...
heartbeat_at: ...
workspace_id: <uuidv7>
environment_fingerprint_id: <uuidv7>
```

States:

- `REQUESTED`;
- `PROVISIONING`;
- `BOOTSTRAPPING`;
- `READY`;
- `BUSY`;
- `DRAINING`;
- `TERMINATING`;
- `TERMINATED`;
- `FAILED`;
- `LOST`.

---

# 18. MCP contracts

## 18.1 `MCPServerSpec`

```yaml
schema_version: 1
mcp_server_id: project.godot
transport: stdio
command:
  executable: bash
  args:
    - scripts/mcp/godot_mcp.sh
working_directory: repository_root
environment_allowlist:
  - PATH
  - HOME
network_policy: loopback_only
startup_timeout_seconds: 240
tool_timeout_seconds: 1800
trust:
  source: repository_config
  level: project_declared_untrusted_until_approved
capability_mapping:
  godot_status: godot.status
  godot_run_project: godot.runtime.run
  godot_screenshot: godot.runtime.screenshot
```

Repository-provided command/args are not automatically trusted to execute. The MCP trust decision is separate from parsing the config.

## 18.2 `MCPProbeResult`

```yaml
schema_version: 1
mcp_probe_id: <uuidv7>
server_id: project.godot
runner_id: <uuidv7>
status: PASS
started_at: ...
completed_at: ...
server_fingerprint:
  package: "@shin2344234/godot-mcp"
  version: "0.1.2"
capabilities_observed:
  - godot.status
  - godot.runtime.run
  - godot.runtime.screenshot
warnings: []
```

Discovery is observational. It does not expand worker authority automatically.

---

# 19. Security and policy contracts

## 19.1 `PolicyDecision`

```yaml
schema_version: 1
policy_decision_id: <uuidv7>
subject:
  type: tool_call
  id: <uuidv7>
decision: ALLOW
reasons:
  - capability granted
  - current fencing token
  - path inside write scope
  - no paid resource required
policy_versions:
  cost: zero_cost_v1
  security: default_repo_v1
  project: <revision>
decided_at: ...
```

Decision enum:

- `ALLOW`;
- `DENY`;
- `REQUIRE_OWNER_APPROVAL`;
- `DEFER_WAIT_CAPACITY`.

## 19.2 `ApprovalRequest`

```yaml
schema_version: 1
approval_request_id: <uuidv7>
kind: ENABLE_PAID_RESOURCE
subject_ref: ...
reason: No free eligible provider capacity remains
requested_change:
  paid_inference_allowed: true
scope:
  run_id: <uuidv7>
expires_at: ...
status: PENDING
```

No UI wording can bypass this durable approval object.

---

# 20. Audit/event contract

## 20.1 `AuditEvent`

```yaml
schema_version: 1
event_id: <uuidv7>
stream:
  entity_type: task
  entity_id: <uuidv7>
sequence: 83
event_type: TASK_STATE_CHANGED
occurred_at: ...
actor:
  type: orchestrator
  id: control-plane-1
correlation:
  run_id: <uuidv7>
  attempt_id: <uuidv7-or-null>
payload:
  from: EXECUTING
  to: VALIDATING
  reason: worker_report_received
redaction:
  secrets_removed: true
```

Rules:

- per-entity sequence is monotonic;
- event append and current-state mutation occur in one database transaction where they represent one logical transition;
- raw prompts and chain-of-thought are not audit payloads;
- high-impact side effects include a receipt/reference.

---

# 21. API error envelope

All Amodex APIs should normalize errors to a stable envelope.

```yaml
schema_version: 1
error_id: <uuidv7>
code: CONFLICT_STALE_REVISION
category: conflict
message: Task revision changed; reload before mutating
retryable: true
retry_after_seconds: null
details:
  expected_revision: 8
  actual_revision: 9
correlation_id: <uuidv7>
```

Core categories:

- `validation`;
- `authentication`;
- `authorization`;
- `policy`;
- `conflict`;
- `capacity`;
- `provider`;
- `runner`;
- `repository`;
- `tool`;
- `dependency`;
- `internal`.

The user-facing message may be localized later; `code` and semantics stay stable.

---

# 22. State-transition authorization matrix

| Entity | Transition | May propose | May authorize |
|---|---|---|---|
| Task | PLANNED -> READY | Director | Orchestrator |
| Task | READY -> LEASED | Scheduler | Orchestrator transaction |
| Task | EXECUTING -> VALIDATING | Worker report | Orchestrator |
| Task | VALIDATING -> CORRECTION_REQUIRED | Validator | Orchestrator |
| Task | CHECKPOINTED -> CANDIDATE | Director/validator policy | Promotion pre-gate |
| Candidate | READY -> PROMOTING | Director/policy | Promotion Gate |
| Candidate | PROMOTING -> PROMOTED | Git result | Promotion Gate after post-validation |
| Paid policy | false -> true | any role may request | Owner only unless future explicit policy says otherwise |
| Path lease | free -> WRITE leased | Task Package | Orchestrator |
| ADR | ACCEPTED -> SUPERSEDED | Director may propose | owner/project architecture policy |

No model output alone is an authorization record.

---

# 23. Contract compatibility tests required before implementation is considered integrated

Every implementation of these contracts must have tests proving at least:

1. unknown authority fields fail closed;
2. old schema versions migrate deterministically;
3. future schema versions are rejected cleanly;
4. immutable fields cannot be changed through update endpoints;
5. stale `revision` mutations fail;
6. stale fencing tokens fail;
7. overlapping WRITE path leases fail;
8. worker cannot grant itself new capability;
9. `ToolResult.UNKNOWN_OUTCOME` triggers reconciliation rather than blind retry;
10. validation evidence cannot be rebound to another commit SHA;
11. paid cost permission cannot be inferred from provider availability;
12. a promotion cannot proceed against a different target SHA without reconciliation;
13. an MCP discovery result does not automatically grant discovered tools;
14. a WorkerReport cannot mark trusted validation evidence as PASS;
15. serialization round-trip preserves all authority-bearing fields exactly.

---

# 24. Frozen Part 2 contract decisions

1. Runtime contracts are versioned and reject unknown future authority schemas.
2. UUIDv7 is the default internal entity identity.
3. Mutable authority objects use revision-based optimistic concurrency.
4. Task state and Attempt state are separate.
5. Worker authority is entirely bounded by `TaskPackage` + active grants/leases.
6. Context capsules are immutable and provenance-bearing.
7. One active WRITE path lease may own a path at a time.
8. Side effects are explicitly classified.
9. Unknown outcomes are a first-class recovery state.
10. Provider calls require Governor authorization in governed mode.
11. Checkpoint existence does not equal validation.
12. Evidence is immutable and bound to exact code/environment provenance.
13. Promotion has its own durable identity and reconciliation lifecycle.
14. MCP discovery and MCP authorization are separate.
15. Policy decisions are durable, inspectable records rather than hidden prompt instructions.

These contracts are the vocabulary the implementation must share. Any future module that invents an incompatible parallel representation requires an ADR and migration plan rather than silent divergence.
