# Amodex Proof-of-Concept Register

Status: **Normative pre-implementation risk register — Part 3/3**

This document defines the PoCs that must retire architectural uncertainty before or during the first implementation stages. A PoC is an experiment with a decision outcome, not a permanent parallel implementation.

Every PoC ends in one of:

- `ADOPT` — planned approach works and can enter production design;
- `ADAPT` — approach works only with specified changes;
- `REJECT` — approach is unsuitable; replacement required;
- `RETEST` — evidence insufficient; blocking if the PoC is marked blocking.

No blocking PoC may remain `RETEST` when its dependent stage exits.

---

# 1. Execution order

Run PoCs in this order because later experiments depend on earlier conclusions:

```text
P0-01 contracts/runtime language
   |
   +--> P0-02 Hermes embedding
   +--> P0-03 free provider/tool calling
   +--> P0-04 PostgreSQL claim/lease/fencing
   +--> P0-05 Git expected-SHA/reconciliation
            |
            v
       P0-06 Northflank control plane
            |
            +--> P0-07 Northflank General Runner
            |
            +--> P0-08 Godot headless Runner
                     |
                     v
                P0-09 Godot visual Runner
                     |
                     v
                P0-10 fallback RunnerBackend
```

Security probes run across all applicable PoCs, not as one final experiment.

---

# 2. P0-01 — executable contract and Python baseline

**Blocking:** yes

## Hypothesis

Python can represent the Part 2 contracts strictly enough for API/storage/runtime use without parallel schema definitions.

## Procedure

Create only the minimum package needed to:

- implement representative strict models for `TaskPackage`, `WorkerLease`, `ToolCall`, `Checkpoint`, and `ValidationEvidence`;
- reject unknown authority-bearing fields;
- serialize deterministic JSON;
- emit schema representation;
- round-trip timestamps/UUIDv7/enums;
- demonstrate schema-version rejection/migration boundary;
- use the same models from a tiny API/test process.

## Pass criteria

- one model definition is reused across process boundaries;
- unknown authority fields fail closed;
- immutable identity fields cannot silently change through update APIs;
- schema-version mismatch is explicit;
- no second hand-written API DTO is needed.

## Failure consequence

If Python/Pydantic cannot satisfy this cleanly, choose another representation before Stage 1 and record ADR.

---

# 3. P0-02 — Hermes embedding and authority isolation

**Blocking:** yes if Hermes remains primary runtime candidate

## Hypothesis

Hermes Agent can be embedded or wrapped as an execution loop while Amodex controls tools, context, providers, task authority, and durable state externally.

## Required probes

1. instantiate/invoke Hermes programmatically without CLI shell dependence;
2. inject a bounded system/context package;
3. expose only a chosen tool subset;
4. disable/deprive delegation capability;
5. receive structured tool-call callbacks/events;
6. interrupt/cancel a run;
7. map provider choice through or around Hermes without losing Provider Governor control;
8. destroy Hermes session/process and prove Amodex task authority remains reconstructable;
9. verify project/user Hermes plugins cannot auto-register unapproved tools in an Amodex worker profile;
10. inspect license/dependency implications for embedding/forking.

## Pass criteria

- Amodex can enumerate/filter final tool exposure;
- Hermes cannot promote Git state or widen scope without Amodex wrappers;
- durable task state does not depend on Hermes SQLite/session DB;
- agent callbacks are sufficient for observable execution;
- no fork is required for core policy isolation, or the required fork surface is small and explicitly accepted.

## Decision branches

- `ADOPT`: upstream Hermes adapter only;
- `ADAPT`: wrapper plus narrow patch/fork;
- `REJECT`: use a smaller custom loop or another runtime substrate while retaining Part 2 contracts.

---

# 4. P0-03 — free provider structured tool-calling path

**Blocking:** yes

## Hypothesis

At least one currently free-eligible remote model/provider can satisfy the minimum worker protocol without requiring paid fallback.

## Providers to evaluate

At least two distinct provider families should be tested where free access is available to the owner, for example:

- Google Gemini;
- Groq;
- NVIDIA NIM/API catalog;
- OpenRouter models explicitly marked free/zero-cost at execution time.

Exact model names must be discovered at PoC execution time, not frozen from this document.

## Test suite

Each candidate performs:

- basic completion;
- strict JSON/structured output task;
- one tool call;
- multi-step tool use;
- malformed tool-argument recovery;
- cancellation/timeout;
- deliberate 401 simulation/config failure;
- deliberate local 429/provider quota handling if safely reproducible;
- capability metadata capture;
- context-size observation;
- latency observation.

## Pass criteria

At least one provider/model supports the minimum Stage 1 path and one fallback candidate exists or the system can queue safely when the primary is unavailable.

No provider is labeled “free” forever. Eligibility is configuration/observation data revalidated during operation.

---

# 5. P0-04 — PostgreSQL job claim, lease, and fencing

**Blocking:** yes

## Hypothesis

PostgreSQL alone can safely provide the initial durable coordination needed for Amodex without Redis.

## Procedure

Use two or more concurrent worker processes against a real PostgreSQL instance.

Prove:

- `FOR UPDATE SKIP LOCKED` or chosen claim mechanism assigns one job to one worker;
- lease heartbeat updates are transactional;
- lease expiry makes work recoverable;
- fencing token increments on reassignment;
- stale worker attempts to update protected state are rejected;
- state transition + event append occur in one transaction;
- process kill during transition leaves a reconcilable state;
- advisory lock use, if any, does not create hidden deadlock dependence.

## Pass criteria

No duplicate active authority and no stale-worker mutation after reassignment.

## Resource observation

Measure connection count and baseline memory under the intended free-host configuration.

---

# 6. P0-05 — Git/GitHub expected-SHA and crash reconciliation

**Blocking:** yes

## Hypothesis

Task branches/checkpoints/promotions can be made safely recoverable using exact SHA observations and remote reconciliation.

## Environment

Use a disposable repository owned by the project account, never Black-Islands `main`.

## Scenarios

1. create branch from exact base SHA;
2. push checkpoint;
3. concurrent branch movement causes expected-SHA failure;
4. simulate client timeout after push and query remote to discover whether push succeeded;
5. create PR from exact candidate;
6. move target branch before promotion;
7. verify stale-base block;
8. simulate merge accepted but DB acknowledgement lost;
9. reconcile merge result without duplicate promotion;
10. verify worker credential/path cannot mutate protected `main` directly.

## Pass criteria

Every ambiguous remote operation has a deterministic observation/reconciliation path.

---

# 7. P0-06 — Northflank minimal Control Plane

**Blocking:** yes for Northflank as primary host; non-blocking for Amodex overall if fallback host selected

## Current baseline to revalidate

As of 2026-10-31 Northflank advertises Sandbox with 2 services, 2 jobs, 1 database, no card requirement, always-on/no sleeping, and limited compute/up to the stated free-resource allowance. This must be rechecked immediately before deployment.

## Hypothesis

A minimal Amodex API + scheduler/control-plane process + PostgreSQL can remain operational within free Sandbox constraints.

## Procedure

Deploy:

- one minimal Control Plane service;
- one PostgreSQL addon/database;
- health endpoint;
- durable test run/event;
- restart/redeploy cycle.

Measure:

- service startup;
- RAM/CPU baseline;
- DB connectivity;
- restart behavior;
- secret injection;
- outbound provider connectivity;
- persistent state after redeploy;
- deployment/API automation feasibility.

## Pass criteria

- no paid resource required;
- service remains reachable without client connection;
- DB survives service restart;
- resource headroom exists for Stage 1 baseline;
- no hidden sleep behavior invalidates persistence assumptions.

## Reject condition

If free compute is insufficient even for Control Plane, choose another zero-cost host/backend before Stage 1.

---

# 8. P0-07 — Northflank General Runner job

**Blocking:** yes for Northflank RunnerBackend, not for Amodex if alternative backend works

## Hypothesis

A Northflank on-demand job can serve as a disposable General Runner within free limits.

## Runner contents

Minimum:

- Python runtime;
- Git;
- agent runtime adapter;
- project checkout;
- basic shell/filesystem tools;
- network access according to policy.

## Procedure

Control Plane requests a job that:

1. starts from clean image;
2. receives opaque task/runner identity;
3. clones a fixture repo;
4. reports heartbeat;
5. runs a deterministic task/test;
6. uploads/report artifacts/results;
7. terminates;
8. is launched again to reconstruct from remote checkpoint.

## Pass criteria

- startup latency acceptable for async work;
- resource use stays inside free envelope;
- no durable truth depends on Runner filesystem;
- lifecycle can be automated from Control Plane;
- termination does not lose pushed checkpoint/evidence metadata.

---

# 9. P0-08 — Godot 4.7.2 headless Runner

**Blocking:** yes for Godot support; not for generic Amodex Stage 1

## Hypothesis

A zero-cost RunnerBackend can execute Black-Islands-like Godot headless validation with the pinned toolchain.

## Target toolchain

For Black-Islands benchmark revalidate project pins at test time. Current known baseline includes:

- Godot 4.7.2;
- Node.js 22+;
- `@shin2344234/godot-mcp` project-pinned release;
- Serena project-pinned version;
- project bootstrap dependencies.

## Procedure

In a read-only clone/benchmark branch:

- run project bootstrap;
- start Godot/editor/LSP configuration required by Serena;
- start Godot MCP;
- execute capability/status probe;
- perform parser/LSP diagnostics;
- execute one existing focused headless test suite;
- collect logs/evidence;
- terminate cleanly.

## Pass criteria

- real MCP process used;
- real Godot binary used;
- project diagnostics observable;
- resource use measured;
- no public editor bridge exposure;
- no project pin upgrade.

---

# 10. P0-09 — Godot visual Runner and screenshots

**Blocking:** blocking only for visual-validation feature; generic Amodex can proceed if assigned to fallback

## Hypothesis

Northflank or another zero-cost Runner can provide a virtual display/software-render path sufficient for Godot windowed tests, input, screenshots, and MCP runtime inspection.

## Procedure

Use Xvfb/equivalent plus software rendering where GPU is unavailable.

Prove:

- Godot opens real windowed project/runtime;
- MCP reports live connection;
- viewport/window dimensions are controlled;
- native input can be sent;
- screenshot is captured;
- screenshot hash/artifact metadata recorded;
- project errors/warnings collected;
- process exits without orphaned display/Godot processes.

## Measurements

Record:

- peak RSS;
- CPU utilization;
- startup duration;
- screenshot duration;
- renderer/driver fallback messages;
- stability over repeated runs.

## Pass criteria

Visual evidence is real, repeatable enough for validation, and free.

## Decision

If Northflank fails but another free RunnerBackend passes, mark `ADAPT` and route only visual workloads there.

---

# 11. P0-10 — fallback RunnerBackend portability

**Blocking:** before claiming Runner portability; may occur during Stage 1/2

## Hypothesis

A task/RunnerSpec can execute on a second backend without task-layer code changes.

## Candidate fallback

GitHub Actions is the initial candidate for bounded CI/validation workloads where account/repository free allowances make it viable. Exact billing/allowance must be checked at execution time, especially for private repositories.

## Procedure

Execute the same deterministic RunnerSpec-equivalent fixture on:

- primary backend;
- fallback backend.

Compare contract-visible lifecycle:

- requested;
- starting;
- running;
- artifact collection;
- completion/failure;
- cancellation if supported.

## Pass criteria

Backend-specific details remain inside backend adapter/configuration and do not leak into task contracts.

---

# 12. Security probes required across PoCs

Every relevant PoC must include at least one negative test from this list:

- repository prompt asks agent to reveal provider secret;
- symlink escapes workspace;
- `../` path escape;
- shell command attempts host/root filesystem mutation;
- MCP advertises tool not granted by TaskPackage;
- stale worker uses old fencing token;
- provider returns malformed tool args;
- webhook/event replay duplicates delivery;
- Git remote operation returns ambiguous timeout;
- artifact is modified after evidence record and hash mismatch is detected.

Security is not postponed to Stage 6.

---

# 13. PoC evidence template

Each PoC conclusion must record:

```yaml
poc_id: P0-08
status: ADOPT
executed_at: ...
source_sha: ...
environment:
  backend: northflank
  image_digest: sha256:...
versions:
  godot: 4.7.2
  godot_mcp: ...
procedure:
  - ...
observations:
  startup_seconds: ...
  peak_memory_mb: ...
  exit_code: 0
artifacts:
  - ...
failures_observed:
  - ...
decision:
  outcome: ADOPT
  rationale: ...
follow_up:
  adr_required: false
```

Do not write “works” without reproducible evidence.

---

# 14. PoC cleanup policy

After decision:

- adopted production logic is reimplemented/promoted into the proper package with tests; do not import `research/` code directly;
- rejected PoC executables are removed unless their failure reproduction is useful;
- retained research must be clearly non-production;
- credentials/test projects are revoked/cleaned;
- cloud jobs/services created solely for PoC are deleted if not part of accepted deployment;
- conclusions remain in version control.

This prevents experimentation from becoming permanent repository debris.