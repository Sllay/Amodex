# Agent Governance and Authority

Status: **Architecture baseline — Part 1/3**

This document defines how Amodex prevents multi-agent chaos, uncontrolled refactors, duplicated work, scope drift, and model-vs-model architecture wars.

## 1. Governance principle

Amodex is not governed by model intelligence. It is governed by explicit authority.

> An agent does not gain permission because it is capable. It gains permission because the Orchestrator granted a bounded capability for a bounded task.

The hierarchy is:

```text
Owner
  |
  v
Technical Design authority
  |
  v
Amodex Technical Director
  |
  v
Deterministic Orchestrator
  |
  +--> scoped worker
  +--> scoped worker
  +--> correction/review worker
  |
  v
deterministic validation and promotion gates
```

## 2. Technical Director

The Technical Director is the highest AI reasoning role inside Amodex.

It is responsible for converting a human engineering request into a safe executable plan.

### 2.1 Director inputs

The Director may receive:

- owner request;
- external technical-design brief;
- project contract;
- repository graph;
- accepted ADRs;
- project state/stability map;
- current branch/checkpoint state;
- provider/runtime availability;
- validation history;
- unresolved risks.

### 2.2 Director outputs

The Director produces structured decisions rather than directly editing arbitrary files.

Typical outputs:

- plan/run objective;
- task DAG;
- Task Packages;
- context requirements;
- worker capability requirements;
- file/path ownership requests;
- architecture decision requests;
- validation plan;
- escalation decisions;
- promotion recommendation.

### 2.3 Director limitations

The Director must not be allowed to:

- bypass path ownership;
- write directly to `main`;
- override cost policy;
- expose secrets;
- mark failed validation as passed;
- silently change accepted ADRs;
- spawn unbounded workers;
- approve its own unsupported factual claims as evidence.

The Director is powerful, not sovereign.

## 3. Orchestrator

The Orchestrator is software, not a personality.

It enforces:

- DAG readiness;
- concurrency;
- leases;
- file ownership;
- retry ceilings;
- model/provider eligibility;
- zero-cost policy;
- runner capacity;
- task state transitions;
- correction scheduling;
- validation ordering;
- Git lifecycle;
- promotion eligibility.

If the Director requests an impossible or unauthorized action, the Orchestrator rejects it with a structured reason.

## 4. Worker model

Workers are temporary scoped executors.

Each worker attempt receives a Task Package containing enough information to do one coherent job.

Workers must not infer standing authority from previous tasks.

### 4.1 Worker categories

Initial conceptual categories:

- repository analyst;
- researcher;
- implementation/coding worker;
- debugging/correction worker;
- review worker;
- Godot specialist;
- visual/runtime validation worker;
- migration/integration specialist.

These are capability profiles. They are not hardcoded to permanent model names.

## 5. Capability-based model routing

A provider model is selected by current task requirements and measured/known capability profile.

Example internal profile:

```yaml
model:
  provider: google
  id: example-model
capabilities:
  coding: 8.8
  debugging: 8.4
  planning: 9.0
  research: 9.5
  vision: 9.4
  tool_use: 8.7
  structured_output: true
  long_context: 9.2
cost_class: free
latency_class: medium
```

The exact values and scoring mechanism belong to Part 2.

The key rule is that roles do not permanently map to providers. Models evolve, quotas change, and task requirements differ.

## 6. No free-form worker debate

Workers do not receive a shared chat room for discussing implementation.

The allowed communication path is:

```text
Worker A -> structured report -> Director/Orchestrator
Worker B -> structured report -> Director/Orchestrator
```

If outputs conflict:

```text
conflict detected
      |
      v
Director identifies exact incompatibility
      |
      v
new integration/correction task
      |
      v
single scoped worker resolves it
```

The system must not ask several agents to argue until consensus emerges.

## 7. File ownership model

A file/path can have one active writer lease at a time.

### 7.1 Writer lease

A lease defines:

- task ID;
- worker attempt;
- exact paths/globs;
- allowed operations;
- expiration/heartbeat;
- fencing token;
- optional read-only dependencies.

Example:

```yaml
write_scope:
  - game/scripts/combat/melee.gd
  - game/scripts/combat/hitbox.gd
read_scope:
  - game/scripts/player/**
  - game/scripts/stats/**
protected:
  - game/scripts/save/**
  - game/scripts/world/**
```

### 7.2 Review/correction slot

A second worker may inspect or later modify the same file only through a defined review/correction phase.

There is no normal state where two writers concurrently modify the same file and Git is expected to resolve the organizational failure afterward.

## 8. Subagent policy

Default delegation depth: 1.

Workers do not recursively spawn independent teams unless the Director explicitly created nested authority for a task class and the Orchestrator supports that topology.

The preferred strategy is for the Director to own global decomposition rather than allowing each worker to recursively invent subprojects.

## 9. Task sizing

A task should be large enough to deliver a coherent behavior and small enough to have clear ownership and validation.

Bad task:

> Improve the whole combat system.

Better task:

> Implement melee hit registration using the accepted combat interface, restricted to the listed files, with one-hit-per-swing acceptance test and existing regressions preserved.

Tasks should avoid micro-fragmenting a single tightly-coupled change across many agents because communication overhead and interface ambiguity can exceed the benefit of parallelism.

## 10. DAG-driven concurrency

Concurrency is a consequence of independent dependencies.

Example:

```text
Repository audit
      |
      v
Interface contract
   /       \
  v         v
Inventory  Resource definitions
   \       /
    v     v
    Crafting
       |
       v
 Integration
       |
       v
 Validation
```

Only tasks whose dependencies are satisfied and whose ownership sets do not conflict may run concurrently.

The target is not “maximum agent utilization.” The target is minimum safe completion time under provider/runner quotas.

## 11. Component stability

Each project component can carry a maturity state:

- `EXPERIMENTAL` — expected to change;
- `ACTIVE` — in normal development;
- `VALIDATED` — acceptance evidence exists;
- `STABLE` — depended on by multiple systems; modification requires stronger justification;
- `FROZEN` — modification requires explicit architectural/owner approval.

A task attempting to modify a protected component must provide a reason category.

Accepted reason categories include:

- `BUG`;
- `FAILING_TEST`;
- `SECURITY_DEFECT`;
- `PERFORMANCE_REGRESSION`;
- `DEPENDENCY_BREAKAGE`;
- `NEW_REQUIREMENT`;
- `USER_REQUEST`;
- `ARCHITECTURE_APPROVAL`.

“Cleaner implementation” is not a reason category.

## 12. Decision ledger

Accepted architecture decisions are recorded as ADRs or equivalent structured decision records.

A later worker/model receives the active decision set and does not reopen those choices merely because it would have selected another pattern.

Reversal process:

```text
new evidence / new requirement
        |
        v
change proposal
        |
        v
impact + migration analysis
        |
        v
Director/Owner approval
        |
        v
new ADR supersedes old ADR
```

History is preserved.

## 13. Correction flow

A failure does not start an argument between coder and reviewer.

Preferred flow:

```text
implementation worker
       |
       v
validator
   | pass
   +----------> checkpoint/candidate
   |
   | fail
   v
error fingerprint + evidence
       |
       v
correction task
       |
       v
correction worker
       |
       v
validator rerun
```

The correction worker receives:

- failing diff/commit;
- failing validator result;
- expected behavior;
- relevant files/interfaces;
- relevant ADRs;
- attempt history summary;
- explicit write scope.

It does not receive a conversational transcript of other agents' opinions.

## 14. Error fingerprint and anti-loop

Equivalent failures must be identifiable across attempts.

A fingerprint can include normalized components such as:

- validator ID;
- exception/error class;
- stable error message tokens;
- affected symbol/path;
- failing assertion/test;
- stack-frame signature.

After a configured number of meaningful attempts with materially equivalent fingerprints, automatic correction stops.

The Director then chooses among:

- stronger/different model;
- additional context;
- targeted research;
- architecture reassessment;
- narrower reproduction;
- human escalation.

Retries that repeat the same patch strategy do not count as meaningful progress.

## 15. Worker reports

Workers return compact structured outcomes.

Conceptual example:

```yaml
task_report:
  task_id: COMBAT-019
  attempt_id: 3
  status: completed
  changed:
    - game/scripts/combat/melee.gd
    - game/scripts/combat/hitbox.gd
  read:
    - game/scripts/player/player.gd
  validations_requested:
    - combat_melee_focused
    - gdscript_diagnostics
  risks: []
  decisions_requested: []
  scope_deviation: false
```

The worker report is not itself validation evidence. It tells the Orchestrator what happened and what should be checked.

## 16. Human approval boundaries

The Owner should not need to approve routine safe actions one by one.

Human approval is reserved for high-impact decisions such as:

- enabling paid resources;
- destructive repository operations;
- changing license model;
- bypassing a promotion gate;
- deleting durable project history;
- deployment to sensitive targets;
- secret/account connection with broader permissions;
- material architectural reversals where owner policy requires it.

The purpose is not to turn the user into a click-confirmation bot.

## 17. Anti-placeholder enforcement

A worker must not satisfy a task by creating disconnected interfaces and TODOs unless the task explicitly asks for an interface-only design artifact.

Implementation acceptance should require behavior through the real call path.

Examples:

- provider integration -> actual request through provider boundary using controlled test credentials or a recorded simulator;
- Git promotion -> real temp repository branch/merge/revalidation path;
- recovery -> deliberately kill worker and observe reclaim;
- MCP integration -> start actual MCP process and execute capability probe;
- web UI -> execute against actual API rather than static mock data when the stage claims integration.

## 18. Governance invariants frozen by Part 1

1. Director plans; Orchestrator authorizes and schedules.
2. Workers do not possess standing repository authority.
3. No free-form agent debate is part of the core execution model.
4. One active writer lease per path.
5. Review/correction is a separate phase.
6. Recursive delegation is off by default.
7. Concurrency follows DAG + ownership + resource availability.
8. Stable components require evidence-backed reason to change.
9. repeated failure escalates rather than loops indefinitely.
10. worker self-reports never substitute for validation evidence.
