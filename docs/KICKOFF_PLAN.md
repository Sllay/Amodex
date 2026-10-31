# Amodex Implementation Kickoff Plan — First 1–2 Days

Status: **Normative kickoff sequence — Part 3/3**

This document defines the first implementation sequence after architecture closure is accepted. It is intentionally narrow. The goal of the first 1–2 days is not to “build Amodex” broadly; it is to retire the highest-risk assumptions and establish one clean executable foundation without repository scaffolding debt.

Implementation does not begin until Parts 1–3 are accepted/merged or the owner explicitly authorizes coding from the architecture branch.

---

# 1. Kickoff objective

At the end of the first implementation block, the repository should have:

- a real Python package only because real PoCs/tests use it;
- locked dependencies;
- strict executable contract models for a representative subset;
- real PostgreSQL test infrastructure;
- Hermes embedding result;
- at least one real free-provider structured/tool-call result;
- Git expected-SHA/reconciliation result;
- a decision on Northflank Control Plane feasibility if credentials/access are available;
- no fake scheduler/Director/worker services created “for later”.

The best outcome is a small repo with several proven architectural decisions, not a large repo with many empty modules.

---

# 2. Pre-kickoff owner prerequisites

Before the first coding session, obtain/configure only what is necessary:

## Required

- GitHub repository access;
- a local/development Python environment or suitable coding Runner;
- Docker/container capability if available for PostgreSQL integration tests, otherwise another free PostgreSQL development instance;
- at least one free provider API credential the owner is comfortable using;
- Git identity for development commits.

## Needed for infrastructure PoCs when reached

- Northflank account/project access;
- Northflank API credential only if job/service automation PoC requires it;
- optional second free provider credential;
- disposable GitHub test repository or permission to create one.

## Needed for Godot PoCs later in Stage 0

- access to clone Black-Islands read-only;
- project bootstrap network dependencies;
- zero-cost Runner candidate with enough resources;
- no Black-Islands write permission required for P0-08 read-only/headless feasibility.

Do not request/provider-load every possible API key in advance.

---

# 3. Branch and checkpoint policy for kickoff

Start from accepted architecture `main`.

Recommended branch:

```text
stage/0-feasibility
```

Individual risky experiments may use work branches:

```text
work/0/contracts
work/0/hermes-embed
work/0/provider-probe
work/0/postgres-leases
work/0/git-reconcile
```

Do not create all branches up front. Create when work starts.

Checkpoint after each PoC conclusion that materially changes the repository.

PoC code under `research/` must not become a hidden dependency of production code.

---

# 4. Day 1 — Block A: repository bootstrap from real need

## Goal

Create only enough project infrastructure to execute P0-01.

## Work

1. create `pyproject.toml` with chosen supported Python range;
2. select package/build tooling;
3. choose dependency manager/lock strategy, preferably one deterministic modern lock workflow;
4. create `src/amodex/contracts/` because executable contracts are immediately needed;
5. create `tests/contract/` with first real tests;
6. add formatter/linter/type/test configuration only when actually invoked by the same commit;
7. create `.gitignore` entries for Python/env/test/cache/local secrets before tools generate them;
8. lock dependencies.

## First executable contract subset

Implement enough to prove contract strategy:

- shared schema/version primitive;
- UUIDv7-compatible identity handling;
- `TaskPackage` representative fields;
- `WorkerLease` + fencing token;
- `ToolCall`/side-effect class;
- `Checkpoint`;
- `ValidationEvidence`.

Do not implement every Part 2 schema during this block.

## Tests

Must include:

- valid serialization;
- unknown authority field rejection;
- future schema version rejection;
- immutable identity/update behavior strategy;
- deterministic JSON round trip;
- enum validation.

## Exit

P0-01 receives `ADOPT`/`ADAPT`/`REJECT` conclusion.

If contract strategy is ugly/duplicated, stop and fix it before adding API/database.

---

# 5. Day 1 — Block B: Hermes embedding PoC

## Goal

Determine whether Hermes remains the primary worker runtime substrate before building production worker architecture around it.

## Isolation

Keep exploratory adapter code under `research/p0-02-hermes/` unless a minimal clean adapter is clearly production-ready and already exercised by accepted tests.

## Procedure

Using the exact upstream version/commit selected at test time:

1. instantiate/invoke the agent loop programmatically;
2. supply one minimal context;
3. register/expose a tiny controlled tool set;
4. verify an unregistered tool is unavailable;
5. inspect plugin/tool auto-discovery implications;
6. verify delegation can be disabled/not exposed;
7. capture tool-call callbacks;
8. cancel/interrupt an execution;
9. discard Hermes process/session;
10. show that PoC-owned task metadata remains outside Hermes session storage.

## Security probe

Place untrusted text in task context asking to invoke a tool that was not exposed.

Expected: the runtime cannot invoke an unavailable tool regardless of text.

## Decision

Record one of:

```text
ADOPT: wrapper only
ADAPT: wrapper + narrow maintained patch/fork
REJECT: use custom/smaller runtime loop
```

Do not postpone this decision while building Stage 2 around an assumed Hermes API.

---

# 6. Day 1 — Block C: provider path PoC

May run in parallel with Hermes PoC only if one person/agent can safely isolate files and provider quota allows it. Otherwise run after Hermes conclusion.

## Goal

Prove one real free provider can execute the minimum structured/tool workflow.

## Build only

- minimal provider abstraction used by PoC;
- minimal secret/config loader;
- local deterministic provider simulator for error tests;
- result capture.

Do not build full Provider Governor yet.

## Live test

For one free provider/model:

1. completion;
2. structured output;
3. one tool/function call or provider-equivalent structured invocation;
4. tool result -> second model turn;
5. timeout/cancel observation;
6. malformed output handling.

## Simulator tests

Generate 401, 403, 429 with Retry-After, 500, malformed JSON/tool args.

## Required output

Record:

- provider/model exact ID;
- date;
- free eligibility source/assumption;
- latency;
- tool/structured support;
- errors observed;
- whether Hermes and provider path integrate cleanly or Amodex should own provider calls outside Hermes.

No paid fallback.

---

# 7. Day 1/2 — Block D: PostgreSQL durability PoC

## Goal

Prove PostgreSQL can replace Redis for initial job/lease coordination.

## Repository additions justified here

Only now create:

- minimal `src/amodex/storage/` code needed for real PostgreSQL test;
- migration infrastructure if a real schema is created;
- `tests/integration/` PostgreSQL tests.

## Minimal schema

Use only entities needed for the PoC, for example:

- jobs/tasks;
- worker leases;
- events;
- fencing counter/state.

Do not attempt full production schema in the first migration.

## Concurrency test

Start at least two worker processes/connections.

Prove:

- one claim;
- heartbeat;
- lease expiry;
- reassignment;
- stale fencing token rejection;
- state+event transactional mutation;
- process kill during lease.

## Exit

P0-04 conclusion and measured DB connection/resource observations.

---

# 8. Day 2 — Block E: Git/GitHub reconciliation PoC

## Goal

Retire one of the highest-risk side-effect problems before full worker implementation.

## Environment

Create/use disposable test repository.

Do not use Black-Islands.

## Procedure

Implement PoC/tool only for:

- observe ref SHA;
- create task branch from exact base;
- commit/push a file;
- simulate/force stale expected SHA;
- query remote after ambiguous operation;
- verify remote result.

If GitHub App setup is not ready, a narrow development credential may be used for PoC, but record that production auth remains GitHub App direction.

## Fault cases

- local says push failed but remote branch actually moved;
- remote unchanged;
- target moved before expected update;
- retry with stale expected SHA rejected.

## Exit

P0-05 conclusion.

Do not implement full Promotion Gate yet.

---

# 9. Day 2 — Block F: Northflank Control Plane feasibility

Run when Northflank access is available.

## Goal

Prove the chosen free host can run the smallest durable Amodex service.

## Minimal service

A tiny real application using the same package/runtime choices that Stage 1 will use.

It needs only:

- `/health/live`;
- `/health/ready`;
- DB connectivity;
- one durable record/event endpoint or test command;
- graceful SIGTERM.

Do not build the final API yet.

## Procedure

1. revalidate current Sandbox terms/resource limits;
2. provision PostgreSQL if PoC uses hosted DB;
3. deploy minimal service;
4. write durable record;
5. restart/redeploy service;
6. confirm record remains;
7. measure idle RAM/CPU and startup;
8. verify no billing/payment required;
9. record exact deployment settings.

## Exit

P0-06 conclusion.

If failed due compute constraints, select alternative zero-cost Control Plane host before Stage 1 rather than forcing architecture around Northflank.

---

# 10. Day 2+ — Block G: Runner PoCs

If time remains in the 1–2 day kickoff, begin P0-07. Otherwise this is the immediate next task; do not skip it and start building Stage 1 worker behavior blindly.

Sequence:

1. General Runner lifecycle;
2. Godot headless;
3. Godot visual;
4. fallback backend if required.

Godot visual is intentionally later because it is resource-heavy and depends on proving the generic Runner lifecycle first.

---

# 11. What must NOT be built during kickoff

Do not create production versions of:

- Technical Director;
- full Task DAG engine;
- multi-worker scheduler;
- Context Engine full indexer;
- all provider adapters;
- web UI;
- full Termux CLI;
- Promotion Gate;
- Godot plugin/UI integration;
- agent marketplace;
- Redis;
- vector database;
- recursive subagents.

The kickoff goal is to remove uncertainty that would otherwise force these systems to be rewritten.

---

# 12. Recommended commit sequence

Example only; commits should follow actual completed coherent work:

```text
chore: initialize executable contract package and locked tooling

test: prove strict runtime contract semantics

research: validate Hermes embedding and authority isolation

research: validate free provider structured tool path

research: prove PostgreSQL lease and fencing semantics

research: prove Git expected-SHA reconciliation

research: validate Northflank control-plane feasibility
```

Do not commit a “full architecture skeleton”.

---

# 13. First checkpoint after kickoff

At the end of the first 1–2 day block, create a Stage 0 evidence record summarizing:

```yaml
stage: 0
checkpoint: kickoff-01
completed_pocs:
  P0-01: ADOPT
  P0-02: ADOPT/ADAPT/REJECT
  P0-03: ...
  P0-04: ...
  P0-05: ...
  P0-06: ...
remaining_blockers:
  - ...
architecture_changes:
  - ADR references
repo_hygiene:
  placeholder_files: 0
  unused_packages: 0
  secrets_found: 0
```

If only three PoCs were completed, record three. Do not fake progress percentages.

---

# 14. Kickoff success criteria

The first implementation block is successful if:

- repository remains small and coherent;
- every created production module has a real call site/test;
- highest-risk assumptions are being replaced with measured evidence;
- Hermes decision is explicit;
- free-provider path is real;
- durable coordination strategy is proven or disproven;
- no paid dependency was introduced;
- no broad architecture had to be rewritten because it was not implemented prematurely.

The kickoff is not judged by lines of code.

---

# 15. Immediate transition into Stage 1

Stage 1 begins only after all blocking Stage 0 PoCs are resolved.

The first Stage 1 production milestone should then be exactly:

```text
create durable run
 -> persist it
 -> scheduler claims it
 -> Provider Governor admits free model
 -> real model response
 -> persist completion/events
 -> disconnect/reconnect client
 -> restart Control Plane
 -> state remains correct
```

Do not add coding tools until this durable remote loop is proven.

That sequence is the foundation every later Amodex capability depends on.