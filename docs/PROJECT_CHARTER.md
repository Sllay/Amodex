# Amodex Project Charter

Status: **Architecture baseline — Part 1/3**

This document defines what Amodex is allowed to become, what it must never silently become, and which invariants future implementation must preserve.

## 1. Product definition

Amodex is a persistent engineering-agent platform for long-running software development. It combines a durable control plane, provider-agnostic model access, scoped workers, repository intelligence, deterministic validation, Git-native recovery, and project-specific tool integrations such as MCP servers.

The target experience is not “chat with an AI that can write files.” The target is closer to a small engineering organization with explicit authority, evidence, ownership, and recoverable execution.

The primary user is the repository owner/director. Amodex is subordinate to that user. During the early project period, an external Technical Designer may provide high-level specifications that the Amodex Technical Director converts into executable engineering tasks.

## 2. Why Amodex exists

The project exists to solve five failures that are common in existing coding-agent workflows:

1. **Cost lock-in** — useful coding sessions become constrained by subscription or usage cost.
2. **Session fragility** — work is coupled to a client session, browser window, local terminal, or transient agent context.
3. **Multi-agent chaos** — several models edit overlapping code, reopen settled decisions, argue, or repeatedly rewrite each other's work.
4. **Context waste** — workers ingest entire repositories and project histories when a narrow task requires only a small dependency slice.
5. **False completion** — repositories fill with scaffolding, disconnected scripts, mocks presented as integrations, or code that was never validated end to end.

Amodex must solve those failures structurally rather than through increasingly long prompts.

## 3. Product invariants

The following are non-negotiable unless the Owner explicitly changes the charter.

### 3.1 Zero-cost default

The normal operating mode costs R$0.

Amodex may use free tiers, free quotas, open-source software, user-provided infrastructure, and free hosted capacity. It must never assume that a paid API, credit balance, subscription, or compute upgrade is acceptable.

When free resources are exhausted, the correct behavior is queue, pause, throttle, or move to another explicitly allowed free backend.

### 3.2 Durable server-side execution

A client such as Termux is a control surface, not the execution authority.

Closing the client, losing mobile connectivity, restarting the CLI, or changing devices must not invalidate the server-side task state.

A durable run therefore cannot be represented solely by an in-memory conversation or an active websocket.

### 3.3 Recoverable state machine

Every substantial task is modeled as persisted state with explicit transitions.

At minimum the future run model must distinguish planning, ready, leased, executing, validating, correction, checkpointed, candidate, promoted, paused, failed, cancelled, and recovery-required states where applicable.

A process restart must reconstruct actionable state from durable storage and repository checkpoints rather than attempting to “remember what the agent was thinking.”

### 3.4 Deterministic authority

LLMs may reason about what should happen. They do not own the permission system that decides whether it may happen.

The following must eventually be deterministic services/policies:

- worker creation;
- concurrency limits;
- task DAG readiness;
- file/path leases;
- protected path enforcement;
- provider quotas and cost policy;
- secret visibility;
- destructive command approval;
- branch ownership;
- checkpoint classification;
- promotion gates;
- validation result state;
- retry ceilings;
- task cancellation.

### 3.5 Minimal-context workers

The Technical Director sees broad project context. Workers do not.

A worker receives the minimum sufficient package for its objective: task contract, relevant project rules, relevant accepted decisions, dependency interfaces/symbols, allowed files, tests, and local implementation context.

This reduces cost, accidental refactoring, prompt injection surface, and unrelated architectural creativity.

### 3.6 Stable systems are protected

A system that has passed its acceptance gate is not perpetually open to reinterpretation.

Amodex will track component maturity/stability. Suggested states are:

`EXPERIMENTAL -> ACTIVE -> VALIDATED -> STABLE -> FROZEN`

The later the state, the stronger the evidence required to modify it.

### 3.7 Git is durable engineering memory

Git is not only a final delivery mechanism. It is a transactional recovery and audit layer.

Every substantial implementation task starts from an explicit base SHA, runs in isolated worktree/branch state, creates recoverable checkpoints, and enters `main` only through promotion.

### 3.8 Evidence is authoritative

A worker report is not proof by itself.

Claims such as “tests pass,” “mobile performance is good,” “the MCP works,” “the provider fallback is safe,” or “restart recovery works” require machine-readable or inspectable evidence produced by the relevant validation layer.

## 4. What Amodex is not

Amodex is not:

- an unrestricted autonomous swarm;
- a system where every role is another LLM;
- a prompt wrapper around a provider API;
- a replacement for Git, CI, compilers, test runners, or static analysis;
- an excuse to build custom replacements for mature tools without need;
- a platform that masks provider limits by endlessly retrying requests;
- a system that treats successful code generation as successful integration;
- a repository generator that creates unused directory trees and empty modules in advance;
- a mandatory local-model platform;
- a system that assumes the Owner owns powerful always-on hardware.

## 5. Initial operating model

The conceptual hierarchy is:

```text
Owner / Creative-Technical authority
              |
              v
      Amodex Technical Director
              |
              v
          Orchestrator
       /       |        \
      v        v         v
 Research   Coding    Correction/Review workers
      \        |         /
       \       v        /
        deterministic tools
   (Git, tests, shell, MCP, LSP, CI)
```

The Technical Director creates plans and authorizes tasks. The Orchestrator executes the plan as a DAG and enforces resource/ownership policy. Workers solve bounded reasoning problems. Deterministic tools establish facts.

## 6. Initial role model

Roles are capability profiles, not permanent model assignments.

### Technical Director

Responsibilities:

- understand owner intent;
- reconcile request with current project contracts;
- decompose work into dependencies;
- decide what context each task requires;
- choose worker capability requirements;
- approve or reject requested scope expansion;
- decide when architectural decisions need ADRs;
- evaluate validation evidence;
- authorize promotion.

The Director should avoid low-value file editing when a scoped worker can do it.

### Orchestrator

Responsibilities:

- materialize task DAGs;
- schedule ready work;
- acquire/release leases;
- enforce provider and runner capacity;
- enforce write scopes;
- track state transitions;
- dispatch validation/correction;
- persist events and heartbeats;
- recover abandoned tasks.

The Orchestrator is primarily deterministic software.

### Workers

Workers are ephemeral problem-solving executors. Examples include coding, research, debugging, review, Godot-specific engineering, visual validation, or repository analysis.

A worker receives no standing authority outside its current task.

### Validators

Validators are not treated as conversational agents when deterministic execution is sufficient.

Tests, build commands, linters, schema validators, Git checks, LSP diagnostics, security scans, and runtime probes should execute without spending an LLM call merely to observe their output.

## 7. Design principles for implementation

### 7.1 Vertical slices before breadth

Each implementation stage must finish with an end-to-end capability that actually works.

Example: provider abstraction is not complete because five adapter classes exist. It is complete when a persisted task can select an eligible free model, execute a structured call, survive retryable failure, record usage, and return evidence through the actual control plane.

### 7.2 Integrations before catalogs

Do not implement a long list of providers, MCP servers, worker roles, or dashboards before one representative integration is real end to end.

### 7.3 Failure behavior is product behavior

Crash, timeout, 429, stale Git base, worker death, DB restart, MCP disconnect, partial tool execution, malformed model output, secret access denial, and validation failure are expected states and must have defined transitions.

### 7.4 Reversible choices first

During early implementation, prefer abstractions that keep infrastructure and providers replaceable:

- RunnerBackend instead of direct Northflank assumptions in task logic;
- ProviderAdapter/Router boundary around upstream runtime behavior;
- repository interfaces around GitHub-specific APIs;
- MCP capability registry rather than Godot-specific logic inside the general orchestrator.

### 7.5 Avoid abstraction without a demonstrated variation

Interfaces are justified when a real boundary is strategic or at least two implementations/failure domains are expected. Do not turn every class into a plugin system preemptively.

## 8. Reference benchmark: Black-Islands

The Black-Islands repository is the primary integration benchmark because it contains characteristics that expose weak agent architecture:

- Godot 4.7.2;
- Serena semantic tooling;
- Godot MCP;
- large living GDD and focused architecture/design documents;
- ADR history;
- phase-specific context files;
- subagent rules;
- generated and persistent world systems;
- transactional inventory/crafting;
- save migrations;
- performance evidence;
- visual/runtime validation;
- small checkpoints before long test gates.

Amodex V1 is not considered proven merely by editing its own repository. Stage 4 must eventually demonstrate a safe bounded task against a Black-Islands-like repository contract without polluting protected branches.

## 9. Licensing direction

Amodex is currently proprietary/source-visible under `LICENSE.md`.

This intentionally differs from the earlier idea of an open-source project. If the Owner later chooses an open-source or dual-license model, that requires an explicit licensing decision before accepting outside contributions or incorporating code under assumptions that conflict with the current proprietary model.

## 10. Architecture closure rule

Before Stage 0 implementation begins, the three documentation passes must collectively answer:

- what components exist and why;
- which upstream systems are reused;
- which data contracts are authoritative;
- which operations are durable/idempotent;
- which states can recover after failure;
- which actor owns every high-impact decision;
- how zero-cost mode is enforced;
- how provider capacity is governed;
- how runners are created and destroyed;
- how repository context is compiled;
- how Git promotion works;
- how MCP and secrets are isolated;
- how the system is tested end to end;
- what must be proven by PoC before implementation decisions freeze.

If those answers are absent, the project remains in design closure rather than compensating with placeholder code.
