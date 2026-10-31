# Amodex System Architecture

Status: **Architecture baseline — Part 1/3**

This document defines the service boundaries, execution topology, durable-state ownership, and runtime responsibilities that future implementation must preserve.

## 1. Architecture goal

Amodex must support long-running engineering work that continues after the user disconnects, survives worker failure, respects zero-cost policy, isolates repository changes, and can integrate project-specific toolchains such as Godot MCP and Serena without coupling the whole platform to Godot.

The central architectural rule is separation between:

- **Control Plane** — durable authority and coordination;
- **Agent Runtime** — reasoning loop and tool-call generation;
- **Runner Plane** — ephemeral execution environments where code and tools actually run;
- **Repository Plane** — Git/GitHub state and promotion history;
- **Evidence Plane** — deterministic validation outputs and artifacts.

## 2. High-level topology

```text
Termux / future Web UI / API client
                 |
                 v
        +-------------------+
        |   Amodex API      |
        | auth + commands   |
        +---------+---------+
                  |
                  v
        +-------------------+
        |   Control Plane   |
        | Director state    |
        | Orchestrator      |
        | Scheduler         |
        | Policy engine     |
        | Provider governor |
        +----+----------+---+
             |          |
             |          +--------------------+
             v                               v
       PostgreSQL                       Provider APIs
  state/events/leases               free-eligible models
             |
             v
        Runner Manager
             |
       +-----+------+----------------+
       |            |                |
       v            v                v
 Northflank      GitHub           future/local
 Runner Job      Actions          RunnerBackend
       |
       v
  Hermes worker runtime
  scoped tools + MCP
  Git worktree
  tests/LSP/runtime
       |
       v
 ValidationEvidence
       |
       v
 Git checkpoint / candidate
       |
       v
 Promotion Gate -> protected integration/main
```

## 3. Control Plane

The Control Plane is the durable brain of the system, but not a single monolithic LLM process.

### 3.1 Responsibilities

The Control Plane owns:

- project registry;
- repository metadata and trusted configuration;
- task DAGs;
- task/run state machines;
- worker leases and heartbeats;
- path ownership/locks;
- provider quotas, cooldowns, budgets, and routing eligibility;
- runner scheduling;
- secret references and capability grants;
- checkpoint metadata;
- validation status;
- Git promotion decisions;
- audit/event log;
- user-visible status.

### 3.2 What must not live only in memory

The following cannot exist solely inside a Python object, Hermes session, websocket, or process-local queue:

- task state;
- task dependencies;
- active leases;
- last durable checkpoint;
- accepted validation evidence;
- provider cooldown state relevant to retry safety;
- promotion state;
- approval requirements;
- high-impact tool outcomes;
- recovery metadata.

A process restart must reconstruct these from durable storage and Git.

### 3.3 Director vs Orchestrator

The Technical Director is a reasoning role. The Orchestrator is a deterministic authority service.

The Director may propose:

- task decomposition;
- model capability requirements;
- context selection;
- architecture changes;
- correction strategy;
- promotion recommendation.

The Orchestrator decides whether those proposals are executable under policy:

- dependencies satisfied?;
- path available?;
- provider eligible?;
- zero-cost capacity available?;
- runner capacity available?;
- branch/base valid?;
- approval required?;
- retry budget exhausted?;

This prevents a smart model from bypassing system invariants.

## 4. Durable storage

### 4.1 Initial database choice

PostgreSQL is the initial authoritative database candidate.

The design intentionally avoids requiring Redis in the first implementation because Northflank Sandbox currently allows one database/addon. PostgreSQL can initially provide:

- relational task state;
- append-only-ish event records;
- transactional job claims;
- row locks;
- `FOR UPDATE SKIP LOCKED` worker claiming;
- advisory locks where appropriate;
- lease metadata;
- fencing-token counters;
- provider usage windows;
- checkpoint/evidence metadata.

Redis may be added later only when measurement demonstrates a real need for lower-latency ephemeral coordination or rate-limit primitives.

### 4.2 Event log and snapshots

Amodex should use both current-state tables and an auditable event stream.

Current-state tables answer “what should happen now?” efficiently. Event records answer “how did we get here?” and support recovery/audit.

Do not attempt full event sourcing for every internal field unless implementation evidence proves the added complexity is worth it.

## 5. Task/run state machine

The exact schema is deferred to Part 2, but the conceptual lifecycle is frozen here.

Representative task states:

```text
DRAFT
  |
  v
PLANNED
  |
  v
READY <-----------------------------+
  |                                 |
  v                                 |
LEASED                              |
  |                                 |
  v                                 |
EXECUTING                           |
  |                                 |
  +--> PAUSED                       |
  |                                 |
  +--> RECOVERY_REQUIRED -----------+
  |
  v
VALIDATING
  | \
  |  +--> CORRECTION_REQUIRED -> READY/LEASED
  |
  v
CHECKPOINTED
  |
  v
CANDIDATE
  |
  v
PROMOTED
```

Terminal states include `FAILED`, `CANCELLED`, and `REJECTED` where appropriate.

State transitions must be transactional and authorized. A worker cannot mark itself `PROMOTED`.

## 6. Worker leases and crash recovery

### 6.1 Lease model

A worker receives a time-bounded lease for a task attempt.

A lease includes:

- task ID;
- attempt ID;
- worker/runner ID;
- fencing token;
- acquisition timestamp;
- heartbeat deadline;
- write-scope lease IDs.

The worker renews heartbeat while active. If the heartbeat expires, the task is not immediately assumed safe to rerun; the Orchestrator first evaluates possible side effects and last durable event.

### 6.2 Fencing

Recovered/reassigned workers must not allow an old process to continue writing after lease loss.

High-impact tool calls and path writes should carry the current fencing token or equivalent authority version. Stale workers fail closed.

### 6.3 Idempotency classes

Tools/actions must be classified:

- **read-only** — safe to repeat;
- **idempotent write** — repeat with same idempotency key yields same intended state;
- **conditionally idempotent** — safe only with expected version/SHA/revision;
- **non-idempotent** — requires durable intent/result recording and explicit recovery logic.

Examples:

- read file: read-only;
- create/update a checkpoint record by deterministic ID: idempotent write;
- update Git branch with expected SHA: conditionally idempotent;
- publish/deploy/send external message: potentially non-idempotent.

A task is not safely recoverable until its side effects are classified.

## 7. Agent Runtime boundary

Hermes Agent is the leading runtime substrate under evaluation, not the authority layer.

Amodex may reuse Hermes for:

- conversation/tool execution loop;
- provider runtime resolution where compatible;
- tool registry;
- MCP client support;
- plugin hooks;
- context-engine extension points;
- session primitives;
- terminal backend abstractions;
- interrupt/callback behavior.

Amodex must not delegate the following solely to Hermes default behavior:

- project task DAG authority;
- file ownership;
- Git promotion;
- cost permission;
- retry policy across durable task attempts;
- architecture decision authority;
- worker concurrency;
- scope enforcement;
- repository stability state;
- cross-worker conflict resolution.

The preferred relationship is **Hermes inside an Amodex-controlled worker runtime**, not “Amodex is a renamed Hermes configuration.”

## 8. Runner Plane

### 8.1 Runner characteristics

Runners are disposable execution environments.

A Runner may contain:

- checked-out repository worktree;
- Hermes worker runtime;
- language runtimes;
- project toolchain;
- test tools;
- LSP/semantic tooling;
- MCP clients/servers;
- sandbox policy;
- artifact capture tools.

Runner-local disk is treated as cache/workspace, not sole source of durable truth.

### 8.2 RunnerBackend

The Control Plane targets a strategic abstraction:

```text
RunnerBackend
  create(spec)
  start(id)
  status(id)
  execute/attach(...)
  collect_artifacts(id)
  terminate(id)
```

The exact interface belongs to Part 2. The key design rule is that task logic must not contain Northflank-specific assumptions.

Initial backend candidates:

- Northflank on-demand jobs;
- GitHub Actions for selected validation workloads;
- local/container backend for development and tests.

A second backend is added only when a real workload requires it; the abstraction exists from the start because hosting portability is strategic.

## 9. Godot runner profile

Godot support is a capability profile layered on a general Runner.

A Godot-capable Runner for Black-Islands-like projects may need:

- Godot 4.7.2 or project-pinned version;
- Node.js 22+ for the selected Godot MCP;
- Serena and its Python/runtime requirements;
- project-specific addons/dependencies;
- Xvfb or equivalent virtual display for windowed validation;
- Mesa/llvmpipe or another compatible software-render path when GPU is unavailable;
- screenshot/artifact tooling;
- enough RAM/CPU for editor bridge + project runtime.

The system distinguishes two execution profiles:

### Headless validation profile

Use for:

- import/open checks;
- parser/LSP diagnostics;
- unit/integration tests that do not require a visible window;
- schema/resource checks;
- CLI/headless runtime proofs.

### Visual validation profile

Use only when needed for:

- UI layout;
- camera behavior;
- visual effects;
- input/touch interaction;
- animation/rendering;
- screenshots;
- runtime scene state that requires the editor/game bridge.

Visual runners are more expensive in CPU/RAM, so zero-cost scheduling should avoid them unless task acceptance requires them.

## 10. MCP topology

MCP servers should normally run inside or adjacent to the Runner that owns the relevant workspace.

For Godot:

```text
Worker runtime
    |
    | stdio/local transport
    v
Godot MCP server
    |
    | loopback/editor bridge
    v
Godot editor/game
```

The Godot editor bridge should not be exposed as a public internet service.

MCP capabilities are registered into an Amodex capability inventory. A task requests capabilities such as `godot.runtime.screenshot` or `gdscript.symbol.rename`; it does not hardcode arbitrary server invocation when a capability contract exists.

## 11. Repository plane

Repository state is durable independently of Runner lifespan.

A task workspace is reconstructed from:

1. repository URL/identity;
2. expected base SHA;
3. task branch/checkpoint ref;
4. durable task metadata;
5. project bootstrap instructions;
6. environment pins.

No task may depend on an unpushed local Runner commit as its only recovery point after the checkpoint policy says work should be durable.

## 12. Evidence plane

Validation outputs are first-class artifacts.

Examples:

- test reports;
- structured diagnostics;
- command exit status;
- logs with bounded retention;
- screenshots;
- performance measurements;
- provider-response metadata excluding secrets;
- Git diff summaries;
- dependency manifests;
- environment version reports.

Evidence is linked to task attempt, commit SHA, runner/environment identity, and validator definition so results cannot be accidentally attributed to the wrong code state.

## 13. Observability

Amodex must be inspectable without reading private chain-of-thought.

Useful observable events include:

- task planned;
- model selected and why;
- provider delayed/rate-limited;
- worker leased;
- files leased;
- tool invoked;
- checkpoint produced;
- validation started/completed;
- correction requested;
- promotion blocked;
- runner recovered/terminated;
- cost-policy denial.

Logs must avoid secrets and excessive raw prompt/context duplication.

## 14. Failure domains

The architecture treats these as independent failure domains:

- client disconnected;
- API service restarted;
- orchestrator restarted;
- database unavailable;
- runner lost;
- provider unavailable/rate-limited;
- model returns malformed tool arguments;
- MCP server crashes;
- GitHub unavailable;
- stale branch base;
- validation tool failure;
- dependency bootstrap failure.

A failure in one domain should not corrupt durable state in another.

## 15. Architectural decisions frozen by Part 1

Unless superseded by ADR:

1. client is not execution authority;
2. PostgreSQL is the initial durable control-plane database candidate;
3. Redis is not required for V1;
4. Hermes is evaluated as a worker runtime substrate, not governance authority;
5. Runners are disposable and created on demand where possible;
6. RunnerBackend portability is strategic;
7. MCP services stay close to the workspace/Runner and follow least privilege;
8. Git checkpoints plus DB state form the recovery boundary;
9. deterministic policies gate LLM proposals;
10. visual validation is a distinct, resource-heavier runner profile rather than the default for every task.
