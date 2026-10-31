# Zero-Cost and Hosting Architecture

Status: **Architecture baseline — Part 1/3**

This document turns “keep Amodex free” into enforceable system behavior rather than a preference.

## 1. Cost invariant

Amodex operates in `zero_cost` mode by default.

In this mode the system must not perform an action that can create a new billable obligation unless the Owner has explicitly changed policy for that specific class of resource.

The policy applies independently to:

- LLM inference;
- cloud compute;
- databases;
- object storage;
- bandwidth/egress where billed;
- third-party APIs;
- build minutes;
- artifact retention;
- observability vendors;
- paid SaaS integrations.

A free tier becoming exhausted is not authorization to continue on a paid tier.

## 2. Cost policy outcomes

For every provider/backend, Amodex must know whether the requested operation is:

- `FREE_ELIGIBLE` — explicitly known to remain within configured free policy;
- `FREE_CAPACITY_EXHAUSTED` — temporarily unavailable but not a fault;
- `PAID_ONLY` — cannot execute under current policy;
- `UNKNOWN_COST` — pricing/capacity could not be proven; fail closed;
- `OWNER_APPROVED_PAID` — explicitly authorized exception with bounded scope.

Default behavior for `PAID_ONLY` and `UNKNOWN_COST` is denial, not optimistic execution.

## 3. Provider behavior under zero-cost mode

Provider routing may use free API keys, free daily/monthly quotas, or free model endpoints only when eligibility is known.

When a provider becomes unavailable:

```text
requested capability
       |
       v
eligible provider A?
  | yes        | no/rate-limited
  v            v
execute      provider B?
                |
          yes --+-- no
          |          |
          v          v
       execute    queue/pause
```

The router must never silently downgrade into a paid equivalent just because the API shape is compatible.

Provider selection is constrained by capability equivalence. A fallback model must satisfy task requirements such as:

- tool calling;
- structured output;
- context size;
- vision;
- reasoning quality class;
- coding/debugging capability;
- supported API mode.

“Available and free” is not enough if the model cannot safely execute the task.

## 4. Hosting baseline: Northflank Sandbox

As of 2026-10-31, Northflank documents its Sandbox plan as:

- free;
- no card required;
- 2 services;
- 2 jobs;
- 1 database/addon;
- up to $20/month of Northflank Cloud resources inside the Sandbox allowance;
- always on / no sleeping;
- compute explicitly described as limited.

Primary sources:

- https://northflank.com/pricing
- https://northflank.com/docs/v1/application/billing/pricing-on-northflank

Northflank is therefore the primary control-plane candidate, not an unquestioned permanent dependency.

## 5. Northflank resource topology

The initial target topology intentionally fits inside the free Sandbox shape.

### Service 1 — Amodex Control Plane

Long-running, lightweight service containing:

- API;
- authentication/session entry point;
- scheduler/orchestrator process or tightly integrated process group;
- Technical Director invocation boundary;
- provider governor;
- repository/project registry;
- status/event streaming.

Whether API and scheduler are one process or supervised sibling processes is an implementation detail to benchmark. They count as one logical service boundary at the hosting level unless reliability evidence requires separation.

### Service 2 — reserved

Do not consume the second service merely because it exists.

It is reserved for a workload that proves it needs independent always-on lifecycle, such as a future web surface, webhook ingress, or worker gateway. Until such a requirement is demonstrated, leaving capacity unused is preferable to architectural clutter.

### Job 1 — General Amodex Runner

On-demand execution for ordinary coding/repository tasks.

Typical contents:

- Git;
- Python/runtime dependencies;
- Hermes worker runtime;
- repository bootstrap;
- shell/file tools;
- language tooling for the current project;
- tests and validators.

### Job 2 — Heavy/Visual Runner

Reserved for workloads with materially different resource or environment requirements, especially Godot visual validation.

The design does **not** require that this be a different implementation forever. The second job boundary exists because heavy visual workloads may need a distinct image, timeout, memory profile, or schedule.

If one image/profile can safely handle both without wasting capacity, the jobs may share code and differ only by RunnerSpec.

### Database — PostgreSQL

Single durable addon for task/run/project/event/checkpoint/provider-governor state.

Redis is excluded from the V1 hosting requirement to preserve the single-addon budget.

## 6. Why the Godot runner is not always on

Keeping Godot editor + project + MCP + Serena + display stack alive continuously would spend scarce RAM/CPU while idle.

The target model is:

```text
Task requires Godot capability
        |
        v
Runner Manager creates/starts job
        |
        v
workspace bootstrap
        |
        v
headless or visual profile
        |
        v
validation/artifact capture
        |
        v
checkpoint + evidence upload
        |
        v
runner terminates
```

This also improves reproducibility because each run starts from an explicit environment definition rather than an editor that has accumulated hidden state for days.

## 7. Godot validation profiles

### 7.1 Headless profile

Preferred whenever acceptance criteria do not require visible rendering.

Capabilities may include:

- project import/open;
- GDScript parser/LSP diagnostics;
- Serena semantic navigation;
- headless smoke tests;
- GUT/GdUnit where supported;
- resource/schema checks;
- project settings checks;
- engine-aware MCP operations that do not need a visual window.

### 7.2 Visual profile

Activated only for tasks where visual/runtime evidence is part of acceptance.

Target stack:

- project-pinned Godot;
- Godot MCP;
- editor bridge;
- virtual display such as Xvfb when required by Linux hosting;
- software rendering path such as Mesa/llvmpipe when no GPU is available and project compatibility allows it;
- screenshot capture;
- deterministic input/replay where practical;
- artifact upload.

The currently selected Godot MCP upstream documents support for Godot 4.7+, testing with 4.7.2, live runtime interaction, screenshots, profiling, debugger features, LSP integration, and GUT/GdUnit4 test execution:

https://github.com/shin2344234/godot-mcp

## 8. Godot MCP placement

The MCP server belongs with the Runner workspace, not as a globally exposed public service.

Preferred topology:

```text
Amodex worker
    |
    | local stdio/process transport
    v
Godot MCP
    |
    | local editor/runtime bridge
    v
Godot editor/game
```

Benefits:

- repository-local version compatibility;
- less exposed attack surface;
- no public editor bridge;
- easier environment teardown;
- task-scoped secrets and permissions;
- one Runner owns one project/editor state.

## 9. Two runners are a starting topology, not a religion

The current Northflank free plan exposes two jobs, so the baseline uses:

1. `general-runner`;
2. `heavy-visual-runner`.

If measurement shows the architecture needs additional runner classes, the design may support more **logical** RunnerProfiles even when a hosting backend maps several profiles onto the same physical job definition.

Conversely, if visual Godot workloads cannot fit within the free Northflank compute envelope, Amodex must not degrade architecture to force them into an undersized container.

Instead the RunnerBackend can route that workload to an explicitly free alternative, while the Control Plane remains on Northflank.

## 10. Fallback runner strategy

Initial backend priority:

1. Northflank Runner Job when free capacity and resources are sufficient;
2. GitHub Actions only for workloads whose repository visibility/minutes/cost policy make the run verifiably free;
3. local/self-hosted runner when the user deliberately provides hardware;
4. queue/pause when no zero-cost backend is eligible.

Amodex must not treat “GitHub Actions exists” as automatically free for every private repository/account. Eligibility must be evaluated against the account/repository policy available at execution time.

## 11. Required infrastructure PoC

Before implementation commits to Northflank as the heavy runner host, execute a dedicated infrastructure proof:

### POC-INFRA-001 — Black-Islands-style Godot runner

Environment:

- free Northflank Sandbox;
- pinned Linux runner image;
- Godot 4.7.2;
- Node.js 22+;
- selected Godot MCP;
- Serena version pinned by target project;
- Xvfb/software-render stack;
- representative project clone/bootstrap.

Must record:

- cold-start time;
- dependency/bootstrap time;
- idle RAM;
- peak RAM;
- CPU behavior;
- disk footprint;
- project import success;
- Serena/LSP query success;
- Godot MCP status;
- headless smoke success;
- visual window launch success;
- screenshot success;
- clean shutdown;
- restart/recreate success.

Outcomes:

- **PASS-A:** both headless and visual profiles fit safely -> Northflank remains primary runner host;
- **PASS-B:** headless fits, visual does not -> keep Northflank for control/general work and route visual profile to another verified-free backend;
- **FAIL:** even general project runner is unreliable -> retain Northflank only if the Control Plane itself remains viable and choose another RunnerBackend.

No architecture document may claim Northflank visual-Godot suitability before this PoC.

## 12. Data durability and ephemeral runner storage

Runner filesystem is not authoritative durable storage.

Before a Runner may be destroyed after meaningful work, required durable state must be in one or more of:

- pushed Git checkpoint;
- PostgreSQL task/event/checkpoint state;
- retained validation artifact storage that is still within zero-cost policy.

A Runner must be reconstructable from repository refs, task state, environment pins, and bootstrap instructions.

## 13. Artifact retention under cost constraints

Artifacts can become a hidden storage cost.

Retention policy must therefore distinguish:

- mandatory long-lived evidence attached to promoted checkpoints;
- temporary debugging artifacts;
- reproducible artifacts that can be discarded;
- user-requested retained screenshots/logs.

Default retention should keep structured summaries and hashes longer than large raw logs/video captures.

## 14. Secrets

Provider keys, GitHub credentials, and hosting credentials are injected through the hosting secret system or equivalent secret store.

Rules:

- never commit `.env` secrets;
- never expose all provider keys to every worker;
- a Runner receives only credentials required for its task;
- MCP subprocesses receive filtered environments;
- logs redact known secret forms;
- context capsules contain secret references, never raw values.

Northflank currently supports secret groups; use them rather than repository-stored runtime secrets.

## 15. Cost observability

Even when monetary cost is zero, scarce quotas are resources.

The Control Plane must track at least:

- provider requests/tokens by model;
- provider remaining known quotas where observable;
- runner minutes/starts;
- active service/resource class;
- storage/artifact growth;
- retries caused by rate limiting;
- queued work blocked by free-tier exhaustion.

The user should be able to answer: “Why is this task waiting?” without inspecting provider dashboards manually.

## 16. Frozen decisions from this document

1. zero-cost is fail-closed by default;
2. Northflank is the primary initial host candidate, not a hard dependency;
3. Control Plane stays lightweight and persistent;
4. heavy project environments are on-demand runners;
5. PostgreSQL occupies the initial single durable database slot;
6. Redis is not required for V1;
7. Godot MCP runs near the project workspace rather than on a public endpoint;
8. headless and visual validation are separate profiles;
9. visual-Godot-on-Northflank remains a PoC requirement, not a proven fact;
10. no paid fallback occurs without explicit owner authorization.
