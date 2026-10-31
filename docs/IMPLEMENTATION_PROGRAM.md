# Amodex Seven-Stage Implementation Program

Status: **Normative implementation program — Part 3/3**

This document converts the Amodex architecture into an executable construction order. A stage is complete only when its vertical slices work through the real integration path and its exit gate is satisfied.

No stage may be declared complete because interfaces, directories, mocks, or diagrams exist.

---

# 1. Program principles

## 1.1 Vertical slices before breadth

Each stage must produce at least one end-to-end behavior that crosses the real boundaries introduced by that stage.

Bad progression:

```text
create every package
create every interface
mock every dependency
call architecture 80% done
```

Required progression:

```text
choose one real path
implement only required modules
execute real path
kill/break it intentionally
recover/validate it
then expand
```

## 1.2 Safety dependencies may move earlier than feature labels

The seven stages describe product growth, but some safety pieces must be implemented earlier than their marketing label suggests.

Examples:

- contract validation begins before multi-agent orchestration;
- secret redaction begins before external provider calls;
- Git expected-SHA behavior begins before full Promotion Gate;
- database migrations begin as soon as durable state exists;
- fault injection begins with the first persistent worker;
- zero-cost enforcement exists before the first real provider request.

These are prerequisites, not optional polish.

## 1.3 Every stage has entry and exit gates

A stage can start only when its required predecessor evidence exists.

A stage can exit only when:

- its required vertical slices pass;
- no unresolved blocker in its scope is hidden as a TODO;
- relevant recovery/security/fault tests pass;
- architecture documents are updated if implementation evidence changed a decision;
- no fake integration is represented as production behavior.

## 1.4 Stage branches

Recommended Amodex implementation branch convention after architecture closure:

```text
main                         validated promoted state
stage/<N>-<name>             stage integration branch when useful
work/<stage>/<task-scope>    isolated implementation branches/worktrees
```

The exact branch policy may evolve by ADR, but direct autonomous worker writes to `main` remain forbidden.

---

# 2. Dependency graph

The high-level implementation DAG is:

```text
STAGE 0 — executable constitution / PoCs
   |
   +--> POC Hermes embedding
   +--> POC provider free path
   +--> POC PostgreSQL lease/recovery
   +--> POC Northflank runner
   +--> POC Godot headless/visual feasibility
   |
   v
STAGE 1 — durable remote runtime + provider layer
   |
   +--> real API -> DB -> durable job -> worker -> provider -> event stream
   +--> disconnect/restart recovery
   |
   v
STAGE 2 — scoped coding worker + tools
   |
   +--> real temp repository task branch/worktree
   +--> scoped read/patch/test/checkpoint
   +--> Hermes adapter if accepted
   |
   v
STAGE 3 — Technical Director + DAG + Context Engine + ownership
   |
   +--> project ingestion
   +--> selective Context Capsule
   +--> multiple dependency-safe tasks
   +--> one-writer enforcement
   |
   v
STAGE 4 — Git validation/checkpoint/promotion V1
   |
   +--> exact-SHA evidence
   +--> Candidate + integration + post-merge validation
   +--> crash/stale-base recovery
   +--> Black-Islands read-only/dry-run benchmark then controlled write benchmark
   |
   v
        AMODEX V1 SAFE
   |
   v
STAGE 5 — specialized workers + capability routing
   |
   +--> research/debug/review/Godot profiles
   +--> measured model routing
   |
   v
STAGE 6 — product surfaces
   |
   +--> Termux daily workflow
   +--> web/project operations
   +--> operational polish
```

Stage 4 is the first point where Amodex is considered safe enough for real autonomous repository changes under its intended governance.

---

# 3. Stage 0 — Constitution, executable contracts, and feasibility PoCs

## 3.1 Purpose

Turn Parts 1–3 from prose into verified implementation assumptions before building the production runtime.

Stage 0 is not a skeleton-app stage. It is a risk-retirement stage.

## 3.2 Entry gate

Required before Stage 0 starts:

- Parts 1, 2, and 3 merged/accepted;
- license accepted;
- zero-cost policy accepted;
- repository layout accepted;
- implementation language baseline accepted or explicitly challenged by PoC;
- no production code exists that needs preserving.

## 3.3 Required PoCs

The exact register is defined in `docs/POC_REGISTER.md`, but Stage 0 must resolve at minimum:

1. Hermes can be embedded/wrapped without giving it authority over Amodex policy.
2. At least one free provider can complete structured tool-calling through the planned adapter path.
3. PostgreSQL can atomically claim work, expire/recover leases, and reject stale fencing tokens.
4. Northflank can host the minimal Control Plane within free constraints.
5. Northflank on-demand job lifecycle can launch and report a General Runner, or a fallback backend is selected.
6. Godot 4.7.2 + selected Godot MCP + Serena can run headless in an available zero-cost Runner.
7. Visual Godot screenshot/input path is either proven on Northflank or assigned to an alternative free RunnerBackend.
8. GitHub expected-SHA/task-branch/checkpoint operations are proven in a disposable repository.

## 3.4 Production artifacts allowed

Stage 0 may create only the minimum production-quality contract/config foundation that is itself exercised by the PoCs.

Examples:

- package metadata;
- executable contract models used by PoCs;
- test harness foundations;
- local PostgreSQL test infrastructure;
- dependency lock;
- research PoC code under `research/`.

It must not create unused scheduler/director/worker packages simply because later stages need them.

## 3.5 Exit gate

Stage 0 exits only when every blocking PoC is `ADOPT`, `ADAPT`, or has a documented replacement decision.

No `RETEST` PoC may block Stage 1 silently.

Required evidence:

- exact dependency versions/digests;
- commands/procedure;
- measured startup/RAM/CPU where hosting matters;
- observed provider behavior;
- failure cases;
- ADR for any Part 1/2 architecture change.

---

# 4. Stage 1 — Durable remote runtime and provider layer

## 4.1 User-visible goal

From Termux or a simple API client, the owner can start a real remote run that survives client disconnection and control-plane process restart, obtains an eligible free model response, and exposes durable status/events.

This is not yet a coding agent.

## 4.2 Required vertical slice S1-V1

```text
client
  -> POST create run
  -> durable Run/Task row
  -> scheduler claims job
  -> worker attempt lease
  -> Provider Governor admits free provider
  -> real model request
  -> response normalized
  -> durable Tool/Model event/result
  -> run completes
  -> client reconnects and reads final state
```

The owner must be able to disconnect immediately after run creation without changing outcome.

## 4.3 Required vertical slice S1-V2 — crash recovery

Procedure:

1. start a deliberately delayed run;
2. confirm worker lease and durable intent;
3. terminate the worker process/container;
4. wait for lease expiry/recovery policy;
5. start replacement worker;
6. reconcile provider/side-effect state;
7. finish exactly once or reach explicit `UNKNOWN_OUTCOME/BLOCKED`;
8. verify no duplicate completion/event corruption.

## 4.4 Required components

Only components needed by these slices:

- canonical contracts;
- FastAPI or accepted API transport;
- PostgreSQL schema/migrations;
- current-state + event persistence;
- scheduler/job claim;
- leases/heartbeats/fencing;
- minimal Provider Governor;
- one real free provider adapter plus deterministic provider simulator;
- secret-safe configuration;
- structured event/status stream;
- minimal remote deployment definition;
- minimal Termux-capable client commands if needed to prove disconnect/reconnect.

## 4.5 Provider acceptance

A provider is not integrated because a generic OpenAI-compatible client can call it once.

The initial production provider path must prove:

- authentication from secret reference;
- model discovery/config;
- structured response/tool-call path needed by later runtime;
- normalized 401/403/429/5xx handling;
- `Retry-After` when supplied;
- quota/concurrency admission;
- zero-cost eligibility;
- no automatic paid fallback;
- usage observation where provider exposes it;
- cancellation/timeout behavior.

## 4.6 Stage 1 exit gate

All must pass:

- real remote deployment works;
- client disconnect does not cancel run;
- control-plane restart reconstructs state;
- worker crash recovery test passes;
- free quota exhaustion yields `WAITING_PROVIDER`, not paid use;
- malformed provider output cannot corrupt authority state;
- secrets are absent from DB event payloads/logs;
- migrations recreate clean DB from zero;
- local deterministic integration suite passes;
- no Stage 2 coding tools are falsely advertised.

---

# 5. Stage 2 — Scoped coding worker and deterministic tools

## 5.1 User-visible goal

Amodex can take one narrowly specified coding task in a disposable test repository, let one worker inspect/edit/test it through enforced scope, create a recoverable checkpoint, and return evidence.

This is the first useful “mini Codex” vertical slice.

## 5.2 Required vertical slice S2-V1

Fixture repository contains:

- a small real Python or mixed-language project;
- existing passing tests;
- one intentionally missing behavior;
- protected file outside task scope.

Flow:

```text
TaskPackage
 -> Runner created
 -> repository cloned at expected SHA
 -> task branch/worktree created
 -> Context Capsule delivered
 -> coding worker invokes trusted read/search/patch tools
 -> path lease enforced
 -> worker changes allowed file
 -> real tests execute
 -> ValidationEvidence created
 -> SAFE then VALIDATED checkpoint pushed
 -> WorkerReport returned
 -> Runner destroyed
 -> new Runner reconstructs checkpoint successfully
```

## 5.3 Mandatory enforcement proofs

The stage test must intentionally ask/induce the worker to:

- write a protected file;
- use `../` path escape;
- follow a symlink outside workspace;
- run an unapproved destructive shell command;
- access a secret not granted to the task;
- create a second writer lease for the same path.

Every attempt must fail at the tool/policy layer regardless of model compliance.

## 5.4 Hermes acceptance

If Hermes is adopted as runtime substrate, this stage must prove through the production adapter that:

- TaskPackage capabilities restrict exposed tools;
- delegation is disabled unless explicitly granted;
- worker callbacks become Amodex events;
- interruption/cancellation works;
- context is injected without treating repository text as system authority;
- Hermes session state is not the authoritative task state;
- a worker can be replaced without requiring hidden conversational state to recover task authority.

If these fail structurally, adopt an isolation/fork strategy through ADR before progressing.

## 5.5 Stage 2 exit gate

Required:

- one real task solved end to end;
- real tests, not model self-report, prove behavior;
- protected path attack suite passes;
- checkpoint survives Runner destruction;
- no direct `main` write path exists for worker credentials;
- scope expansion requires new authorization/package revision;
- failed task does not leave dirty shared workspace;
- correction attempt can take over the same task branch after writer release;
- repository hygiene scan passes.

---

# 6. Stage 3 — Technical Director, DAG, Context Engine, and ownership

## 6.1 User-visible goal

The owner can provide a larger engineering objective. The Technical Director ingests the project contract, produces a bounded task DAG, and Amodex executes independent tasks concurrently only when ownership/dependencies permit.

## 6.2 Required vertical slice S3-V1 — project ingestion

Use a medium fixture repository with:

- root `AGENTS.md`;
- goals/plan docs;
- focused architecture docs;
- ADRs;
- source modules and tests;
- one intentionally conflicting/stale document.

Amodex must produce:

- `ProjectContract`;
- repository map;
- authority map;
- decision index;
- symbol/dependency observations;
- explicit ingestion conflict instead of silently choosing a contradictory rule.

## 6.3 Required vertical slice S3-V2 — Context Compiler

For a narrow coding task, prove that:

- full GDD/full repo are not blindly loaded;
- required root contract is included;
- focused spec wins according to project authority;
- relevant source symbols/interfaces are included;
- unrelated large files are excluded with reason;
- every section has provenance/blob/hash/lines where applicable;
- stale repository SHA invalidates or recompiles the capsule.

Measure context size versus a naive full-context baseline.

## 6.4 Required vertical slice S3-V3 — DAG concurrency

Plan at least three tasks:

```text
A prerequisite interface task
      |
      +--> B independent implementation
      +--> C independent implementation
                 |
                 v
              D integration
```

Prove:

- B and C can run concurrently only if write sets do not overlap;
- D cannot run before dependencies succeed;
- provider/runner caps can serialize otherwise-ready work without corrupting DAG state;
- overlapping path request is blocked;
- a failed B creates correction/replan flow without restarting unrelated C.

## 6.5 Director quality requirements

The Director output is accepted only after deterministic structural validation:

- every task has objective/non-goals;
- dependencies form an acyclic graph;
- acceptance criteria exist;
- requested capabilities exist or are explicitly unavailable;
- write scopes do not conflict for tasks planned concurrently;
- stable/frozen component changes include reason category;
- architecture changes are surfaced, not hidden inside implementation tasks.

## 6.6 Stage 3 exit gate

Required:

- ingestion works on at least two structurally different repositories;
- Context Capsule selective-disclosure test passes;
- task DAG executes with safe concurrency;
- one-writer rule is enforced centrally;
- repeated equivalent error fingerprint escalates after configured attempts;
- no worker-to-worker free chat channel is required;
- Director cannot bypass cost/scope/promotion policy;
- task planning remains recoverable after Director process/model failure.

---

# 7. Stage 4 — Git validation, checkpoints, promotion, and V1 safety

## 7.1 User-visible goal

Amodex can complete real repository work from planning through controlled promotion while proving exactly what commit was validated and surviving stale bases/crashes.

This stage defines **Amodex V1 Safe**.

## 7.2 Required vertical slice S4-V1 — Candidate lifecycle

```text
implementation task
 -> WIP checkpoint if needed
 -> SAFE checkpoint
 -> validators
 -> VALIDATED checkpoint
 -> Candidate entity
 -> target freshness check
 -> integration simulation
 -> post-integration validators
 -> Promotion intent
 -> expected-SHA target mutation/merge
 -> Promotion result/reconciliation
```

All evidence must bind to exact source/integration SHAs.

## 7.3 Required vertical slice S4-V2 — stale base

Procedure:

1. Task A starts from target SHA `T0`.
2. Task B is promoted to target `T1`.
3. Task A produces validated candidate from `T0`.
4. Promotion detects target mismatch.
5. Relevant-dependency analysis decides safe integration vs replan.
6. integration result is revalidated.
7. no blind force/update occurs.

Test both non-overlapping and contract-breaking target changes.

## 7.4 Required vertical slice S4-V3 — promotion crash

Inject crashes at:

- after promotion intent persisted but before GitHub call;
- after GitHub accepted merge/ref update but before DB result acknowledgement;
- during post-integration validation.

Recovery must reconcile remote state before retrying.

No duplicate logical Promotion may be created for the same idempotency identity.

## 7.5 Required Black-Islands benchmark progression

Stage 4 uses `Sllay/Black-Islands` in increasing authority:

### Phase BI-0 — read-only ingestion

No branch creation, no writes.

Amodex must correctly infer/enforce:

- runtime root `game/`;
- Godot 4.7.2;
- progressive disclosure;
- existing Git branch/checkpoint policy;
- Serena preference;
- Godot MCP role;
- no silent dependency upgrades;
- phase Definition of Done.

### Phase BI-1 — dry-run planning

Given a historical/known scoped task, Amodex produces a Task DAG/Context Capsules/validation plan without execution.

Compare with the project's existing harness contract and ensure no unrelated Season/phase scope is introduced.

### Phase BI-2 — isolated benchmark branch

Only after owner approval, use a benchmark branch/worktree, never `main`.

Perform a tiny reversible task selected specifically for Amodex validation.

Must use real project bootstrap/tools and produce evidence compatible with Black-Islands conventions.

### Phase BI-3 — recovery benchmark

Repeat a bounded task while intentionally terminating the Runner after a SAFE checkpoint and prove reconstruction.

## 7.6 Stage 4 exit gate — Amodex V1 Safe

All must pass:

- Candidate/Promotion exact-SHA lifecycle;
- stale-base scenarios;
- promotion crash reconciliation;
- secret/repository hygiene gate;
- post-integration revalidation;
- rollback/revert exercise;
- Black-Islands BI-0 and BI-1 pass;
- BI-2 passes if owner authorizes write benchmark;
- no production worker can bypass Promotion Gate;
- complete run timeline can be reconstructed without chain-of-thought;
- zero-cost invariant remains intact under all benchmark paths.

At this point Amodex may be used for carefully scoped real project work.

---

# 8. Stage 5 — Specialized workers and capability-based routing

## 8.1 Purpose

Improve quality and efficiency after the single-worker/governance architecture is proven.

This stage must not introduce an uncontrolled swarm.

## 8.2 Initial specialized profiles

Candidates:

- repository analyst;
- research worker;
- implementation worker;
- debugger/correction worker;
- reviewer;
- integration/migration worker;
- Godot specialist;
- visual/runtime validation worker.

Profiles are combinations of capabilities/context/policy. They are not permanent provider/model identities.

## 8.3 Model capability routing

Routing becomes production-worthy only when based on observed measurements and hard capability requirements.

At minimum track:

- structured tool-call reliability;
- coding task pass rate;
- correction success;
- planning/schema compliance;
- vision support when required;
- context capacity;
- latency;
- quota availability;
- free-tier eligibility.

A model is never selected only because it is ranked “smartest” globally.

## 8.4 Stage 5 vertical slice

Run a task set where:

- Director assigns research to a research-capable profile;
- implementation uses a coding profile;
- a deliberately introduced failure goes to correction profile;
- reviewer sees contract + diff + evidence rather than implementation transcript;
- Orchestrator remains sole authority for sequencing/ownership.

Compare quality, requests, wall time, and quota consumption against the Stage 4 single-profile baseline.

## 8.5 Exit gate

Required:

- specialization improves at least one measured dimension without safety regression;
- fallback when a preferred model/provider is unavailable works;
- no profile gains standing authority;
- max concurrency remains centrally bounded;
- no two writers overlap;
- context remains role-specific;
- free quota usage remains observable and fail-closed.

---

# 9. Stage 6 — Product surfaces and daily-use operations

## 9.1 Purpose

Turn the engineering system into a usable product without moving authority into clients.

## 9.2 Termux vertical slice

The owner can:

```text
amodex project add <repo>
amodex run "objective"
amodex status <run>
amodex attach <run>
amodex pause <run>
amodex resume <run>
amodex approve <action>
amodex promote <candidate>
```

Closing Termux must not affect execution.

Reconnect must restore state from API, not local terminal history.

## 9.3 Web vertical slice

Web UI should initially project existing API/state rather than inventing separate behavior.

Minimum useful screens:

- projects;
- active/completed runs;
- task DAG/status;
- provider availability/quota state;
- Runner status;
- checkpoints/candidates/promotions;
- validation evidence/artifacts;
- approvals/blockers;
- event timeline.

The UI does not need an IDE/code editor for Stage 6 completion.

## 9.4 Operational features

Stage 6 also closes:

- project/provider configuration UX;
- secret connection flow;
- error messages and recovery guidance;
- retention policies;
- backup/restore procedure;
- health/status endpoints;
- upgrade/migration runbook;
- accessible audit trail;
- user-safe cancellation/pause semantics.

## 9.5 Exit gate

Amodex is considered daily-use product quality when:

- common workflow requires no direct DB/container access;
- status is understandable from CLI/web without raw logs;
- recovery can be initiated/observed safely;
- approvals are explicit and explain impact;
- zero-cost state is visible;
- deployment can be reproduced from runbook;
- backup/restore is proven;
- upgrade path is proven on a copy of persistent state;
- Black-Islands benchmark remains green after product-layer changes.

---

# 10. Stage completion evidence format

Every stage closes with a build/evidence record containing:

```yaml
stage: 2
status: passed
baseline_sha: ...
final_sha: ...
vertical_slices:
  - id: S2-V1
    result: pass
validators:
  - id: contract-suite
    result: pass
  - id: security-path-escape
    result: pass
fault_injection:
  - worker_kill_after_checkpoint: pass
resource_observations:
  peak_memory_mb: ...
known_limits:
  - visual Godot validation uses fallback backend
adrs_created: []
rejected_claims:
  - physical mobile performance not validated
```

This is evidence, not release marketing.

---

# 11. Cross-stage anti-placeholder gate

At every stage review, fail the stage if any claimed feature relies on one of these patterns:

- returning constant/fake success;
- mocked DB when persistence is the behavior under test;
- mocked GitHub when remote reconciliation is the behavior under test;
- mocked provider when claiming provider integration;
- mocked MCP when claiming Godot capability;
- test that calls internal function while claiming API-to-worker integration;
- code path not reachable from any real entry point;
- implementation only exercised by an ad-hoc script that is not part of accepted validation;
- files created for “future use” with no current caller;
- acceptance criterion changed to match implementation failure without explicit design decision.

Mocks/simulators remain valuable for deterministic unit/fault tests, but they cannot be the sole evidence for the external integration they simulate.

---

# 12. Decisions intentionally deferred beyond V1

The following do not block Stage 4/V1 unless implementation evidence proves otherwise:

- Kubernetes;
- Redis;
- microservice decomposition of the Control Plane;
- vector database for repository context;
- autonomous recursive subagent trees;
- marketplace/plugin ecosystem;
- full browser IDE;
- paid model optimization;
- distributed multi-region control plane;
- enterprise RBAC/SSO;
- arbitrary user-hosted Runner fleet.

Deferring these is deliberate scope control, not a placeholder.

---

# 13. Program success criterion

The program succeeds when Amodex can take a real repository objective and produce a recoverable, scoped, validated, promotion-ready result while:

- spending R$0 by default;
- surviving client/worker/control-plane interruption;
- respecting repository authority and accepted decisions;
- using only granted paths/capabilities;
- proving behavior with evidence bound to exact code state;
- refusing unsafe/ambiguous side-effect replay;
- keeping `main` protected;
- avoiding unnecessary context and agent concurrency.

Anything less is a prototype milestone, not the completed Amodex architecture.