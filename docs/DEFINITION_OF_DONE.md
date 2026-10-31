# Amodex Definition of Done

Status: **Normative completion policy — Part 3/3**

This document defines what “done” means in Amodex development. It exists to prevent partial scaffolding, optimistic status reports, disconnected modules, hidden regressions, and fake integrations from being counted as progress.

Amodex uses layered completion gates:

```text
code change
 -> task done
 -> module/capability done
 -> vertical slice done
 -> stage done
 -> V1 safe
 -> product/daily-use done
```

Passing a lower layer never automatically implies the higher layer.

---

# 1. Universal completion rules

No work item is done unless all applicable statements are true:

1. the behavior is reachable through a real accepted call path;
2. acceptance criteria are explicit;
3. validation was actually executed;
4. evidence is bound to the exact code/config state tested;
5. no unauthorized scope was changed;
6. no unresolved parser/type/test error is hidden as “known” without approval;
7. external limitations are stated honestly;
8. no secret/generated junk/local path pollution was introduced;
9. recovery implications are understood for any side effect;
10. documentation/contracts/ADR are updated when behavior changed their truth;
11. the change can be reconstructed from committed/pinned source and documented dependencies;
12. no placeholder implementation is represented as complete.

---

# 2. What is explicitly not Done

The following never qualify by themselves:

- a class/interface exists;
- a directory exists;
- endpoint returns HTTP 200 with fake payload;
- test passes because dependency is fully mocked;
- worker/model says the task succeeded;
- code compiles but is not connected;
- a screenshot looks plausible;
- `TODO` says remaining integration is “small”;
- happy path works but crash/retry can duplicate side effects;
- provider was called once manually outside the production adapter;
- Git commit exists without validation classification;
- branch merged without exact-SHA evidence;
- Godot project opened locally but remote Runner path is untested;
- dashboard says container is running;
- broad refactor leaves tests green but changed unrequested architecture;
- “temporary” debug script is committed with no removal/call-site policy.

---

# 3. Task Definition of Done

A single implementation/correction task is Done when:

## Scope

- TaskPackage objective/non-goals honored;
- all changed files are within granted scope or approved expansion;
- protected files untouched unless explicitly authorized;
- active writer lease released/closed correctly;
- no unrelated cleanup/refactor included.

## Behavior

- every acceptance criterion has validator/evidence or explicitly approved manual evidence type;
- behavior works through real production path, not test-only bypass;
- relevant regressions pass.

## Repository

- work is committed to task branch/checkpoint as policy requires;
- secrets/generated junk scan clean;
- task branch has no accidental local artifacts;
- exact base/final SHA recorded.

## Recovery

- required checkpoint exists before Runner teardown;
- non-idempotent external effects have durable result/reconciliation state;
- no hidden local-only state is required to continue.

## Reporting

WorkerReport records:

- files changed/read;
- requested/executed validators;
- risks;
- scope deviations;
- decisions requested;
- known limitations.

WorkerReport is not allowed to fabricate ValidationEvidence.

---

# 4. Module/capability Definition of Done

A module such as Provider Governor, Git service, Context Engine, RunnerBackend, or MCP adapter is Done only when:

1. it has at least one real consumer;
2. public contract is versioned/defined;
3. unit tests cover core deterministic policy;
4. contract tests cover boundary inputs/outputs;
5. one real integration path executes where the module claims external integration;
6. negative/error behavior is tested;
7. relevant fault/recovery behavior is tested;
8. observability events/errors are structured and secret-safe;
9. module has one clear authority and does not duplicate another module;
10. documentation matches implementation;
11. unused experimental code removed from production package;
12. dependencies are pinned/locked according to repository policy.

---

# 5. External integration Definition of Done

## Provider integration

Done only when production adapter proves:

- auth secret resolution;
- real request;
- required structured/tool behavior;
- normalized errors;
- timeout/cancel;
- 429/cooldown path;
- zero-cost eligibility enforcement;
- no paid fallback;
- usage/latency observations where possible.

## GitHub integration

Done only when disposable remote repo proves:

- task branch creation from exact SHA;
- push/checkpoint;
- expected-SHA conflict;
- ambiguous push reconciliation;
- PR/candidate projection if supported;
- stale-target detection;
- permission boundary preventing worker direct main write.

## Runner backend

Done only when real backend proves:

- create/start;
- authenticated registration;
- capability probe;
- heartbeat;
- task execution;
- artifact/result capture;
- cancellation/termination;
- loss/recovery behavior.

## MCP integration

Done only when real MCP process proves:

- startup/discovery;
- capability filtering;
- actual allowed operation;
- crash/malformed response handling;
- no extra advertised capability becomes authorized automatically.

## Godot integration

Headless Done requires real Godot + project diagnostics/tests.

Visual Done separately requires real window/display/input/screenshot path.

One does not imply the other.

---

# 6. Vertical-slice Definition of Done

A vertical slice is Done when a user-visible/system-level behavior crosses all intended layers.

Example Stage 2 coding slice:

```text
API/task
 -> scheduler
 -> Runner
 -> runtime/Hermes
 -> trusted tools
 -> repository edit
 -> real validator
 -> evidence
 -> checkpoint push
 -> Runner destroy
 -> reconstruction
```

If any layer is replaced by a fake while the slice claims that layer is proven, the slice is not Done.

Required evidence:

- start/end SHA;
- executed commands/tests;
- pass/fail outputs summarized;
- relevant event timeline;
- failure injection where specified;
- known limits;
- environment fingerprint.

---

# 7. Stage 0 Definition of Done

Stage 0 passes when:

- architecture closure accepted;
- executable contract baseline exists;
- all blocking PoCs have decisions;
- Hermes strategy accepted/rejected explicitly;
- at least one free provider path proven;
- PostgreSQL lease/fencing PoC proven;
- Git remote reconciliation PoC proven;
- Control Plane hosting backend selected;
- Runner backend strategy selected;
- Godot headless/visual support capability states are honestly classified;
- any architecture change from PoCs has ADR.

No requirement to have full product API/worker yet.

---

# 8. Stage 1 Definition of Done

All required:

- remote API creates durable run;
- PostgreSQL is authoritative;
- scheduler claims work transactionally;
- real free provider path works;
- Provider Governor fail-closed cost path works;
- client disconnect does not stop run;
- Control Plane restart recovers state;
- worker crash path is proven;
- logs/events do not expose secrets;
- migrations work from clean DB;
- deployed Control Plane has readiness/liveness;
- no paid resource is required.

---

# 9. Stage 2 Definition of Done

All required:

- real scoped coding task solved end to end;
- worktree/task branch isolated;
- trusted file/shell tools enforce scope;
- path/symlink/security negatives pass;
- one writer per path enforced;
- real tests produce ValidationEvidence;
- SAFE/VALIDATED checkpoints are durable;
- Runner destruction/reconstruction works;
- Hermes runtime boundary is proven if adopted;
- worker cannot promote or write `main` directly.

---

# 10. Stage 3 Definition of Done

All required:

- ProjectContract ingestion on at least two repositories;
- documentation/ADR authority/conflict handling works;
- Context Capsule selective disclosure works;
- token/context reduction measured;
- Director produces structurally valid DAG;
- Orchestrator rejects invalid/conflicting plans;
- safe parallel tasks execute;
- overlapping writers blocked;
- correction/anti-loop/error fingerprint path proven;
- Director failure does not destroy durable run state.

---

# 11. Stage 4 / Amodex V1 Safe Definition of Done

This is the critical V1 gate.

All required:

## Git lifecycle

- WIP/SAFE/VALIDATED semantics implemented;
- Candidate is a distinct entity;
- Promotion is a distinct durable operation;
- exact base/candidate/integration/target SHAs recorded;
- stale base handled;
- post-integration validation works;
- rollback/revert exercised.

## Recovery

- ambiguous push reconciles;
- promotion crash before/after remote success reconciles;
- stale worker cannot mutate;
- UNKNOWN_OUTCOME blocks unsafe replay.

## Security

- secrets/path/shell/MCP/prompt-injection critical tests pass;
- repository hygiene gate passes;
- protected main cannot be bypassed by normal worker authority.

## Benchmark

- Black-Islands BI-0 passes;
- Black-Islands BI-1 passes score/critical gates;
- BI-2 passes if owner authorizes write benchmark before V1 declaration, otherwise V1 explicitly carries “Black-Islands write benchmark not yet authorized” limitation;
- Godot capability support is labeled headless/visual according to real PoCs.

## Cost

- all V1 required paths operate at zero cost under current configured free resources;
- exhaustion waits/blocks rather than spends.

When these pass, Amodex can be described as V1 Safe for carefully scoped real project work.

---

# 12. Stage 5 Definition of Done

Specialization is Done only when:

- profiles are capability-based;
- measured benchmark demonstrates improvement;
- routing respects hard task requirements;
- provider fallback preserves tool/context/vision requirements;
- no new free-form agent debate path exists;
- concurrency remains centrally controlled;
- specialized workers cannot widen authority;
- zero-cost usage remains bounded/observable.

If specialization adds complexity without measurable value, Stage 4 single-profile behavior remains preferred.

---

# 13. Stage 6 Definition of Done

Daily-use product surface Done requires:

- Termux workflow works without direct server shell access;
- web UI projects same control-plane authority;
- project/run/task/evidence/promotion status understandable;
- pause/resume/cancel semantics proven;
- approvals explain impact;
- provider/Runner free-capacity state visible;
- backup restore tested;
- upgrade/migration tested;
- deployment runbook reproducible;
- operational health and recovery documented;
- Black-Islands regression benchmark remains green.

---

# 14. Documentation Definition of Done

A technical document is Done when:

- it has a clear authority/status;
- statements distinguish fact, design decision, hypothesis, and measured observation;
- version-sensitive facts include date/source or revalidation instruction;
- terminology matches runtime contracts;
- contradictions with authoritative docs are resolved/superseded explicitly;
- examples do not accidentally become undocumented behavior;
- no section exists only as “TODO: decide later” when that decision blocks implementation.

Open non-blocking future decisions are allowed if clearly scoped beyond the current stage.

---

# 15. ADR Definition of Done

An ADR must contain:

- context/problem;
- decision;
- alternatives considered;
- reasons/tradeoffs;
- consequences;
- migration/compatibility impact;
- status (`proposed`, `accepted`, `superseded`, `rejected`);
- supersedes/superseded-by relation when applicable;
- evidence/PoC references for evidence-driven decisions.

An ADR is not a retroactive excuse for an accidental implementation change.

---

# 16. Correction Definition of Done

A correction attempt is Done when:

- original failure fingerprint is no longer present;
- targeted validator passes;
- relevant regressions pass;
- correction remains in scope;
- no equivalent new failure was introduced;
- attempt strategy/result is recorded;
- repeated-failure threshold is respected.

Changing the test to stop detecting the defect is not a correction unless the requirement itself was formally changed.

---

# 17. Security Definition of Done

For a security-relevant change:

- threat/abuse case identified;
- exploit/negative fixture exists where practical;
- fix is enforced below model/prompt layer;
- regression test proves old bypass fails;
- logging does not leak attack payload secrets unnecessarily;
- compatibility impact reviewed.

A prompt instruction alone is never considered a completed security control.

---

# 18. Performance Definition of Done

Performance work is Done only when:

- benchmark workload defined;
- baseline measured;
- environment identified;
- change measured against baseline;
- regression/variance understood;
- target platform claim matches environment.

Examples:

- desktop llvmpipe measurement may support “desktop software-render proxy observation”;
- it cannot support “physical Android/iPhone performance validated”.

This principle mirrors the evidence honesty expected from Black-Islands.

---

# 19. Zero-cost Definition of Done

A feature path satisfies zero-cost requirement when:

- every external dependency used by default path is free under current configuration/allowance;
- paid alternatives are marked ineligible by default;
- exhaustion produces wait/block/fallback-to-free rather than spend;
- owner can inspect cost-policy decision;
- no payment/card upgrade is necessary to demonstrate required V1 behavior;
- version-sensitive free-tier assumptions are revalidated and recorded.

“Usually free” is not sufficient.

---

# 20. Repository cleanliness Definition of Done

Before Candidate/Stage close:

- no secrets;
- no `.env`;
- no local DB dumps;
- no untracked meaningful source required for behavior;
- no logs/caches/screenshots in wrong roots;
- no unused placeholder files;
- no duplicate implementations left after migration/refactor;
- no dependency archive/vendor dump unless intentionally licensed/pinned;
- no accidental absolute local path;
- no dead experimental code imported by production.

---

# 21. Final architecture-closure Definition of Done

Parts 1–3 are complete when the repository contains, without production scaffolding:

- product/authority architecture;
- runtime contracts;
- provider/recovery/security/Runner/GitHub protocols;
- final target repository layout;
- seven-stage implementation program;
- ordered PoC register;
- test/fault matrix;
- Black-Islands benchmark;
- deployment runbook;
- Definition of Done;
- first implementation kickoff plan;
- updated README/PR describing closure;
- no unresolved contradiction among those documents.

Only after this gate does “start implementation” mean creating production packages.