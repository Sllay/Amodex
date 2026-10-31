# Amodex Repository Layout and Module Boundaries

Status: **Normative architecture contract — Part 3/3**

This document defines the physical repository layout that implementation stages must converge toward. It is a target architecture, not permission to create empty directories or placeholder modules.

## 1. Core rule: structure follows working vertical slices

Amodex must not create a complete directory tree on day one.

A directory or package is created only when at least one real, integrated implementation unit belongs there and the corresponding stage entry gate is open.

Forbidden repository pollution includes:

- empty packages created only to mirror an architecture diagram;
- files containing only `TODO`, `pass`, `NotImplementedError`, fake success responses, or placeholder interfaces without a consuming path;
- duplicated experimental copies such as `provider_new.py`, `provider_v2.py`, `provider_final.py`;
- scripts that are not called by tests, bootstrap, development, deployment, or a documented operator workflow;
- generated logs, caches, screenshots, virtual environments, provider credentials, local databases, editor state, or downloaded tool binaries committed without explicit repository policy;
- vendored third-party source when a pinned dependency is sufficient;
- abandoned proof-of-concept code silently left in production packages.

A PoC that is not promoted into the production design must be removed or retained only as bounded research evidence under `research/` with an explicit conclusion.

## 2. Target top-level layout

The intended mature repository is:

```text
Amodex/
├── AGENTS.md
├── README.md
├── LICENSE.md
├── pyproject.toml                  # created in Stage 1 when Python runtime starts
├── uv.lock                        # created with the first real dependency lock
│
├── src/
│   └── amodex/
│       ├── api/
│       ├── control_plane/
│       ├── contracts/
│       ├── context_engine/
│       ├── director/
│       ├── git/
│       ├── providers/
│       ├── runners/
│       ├── runtime/
│       ├── security/
│       ├── storage/
│       ├── tools/
│       ├── validation/
│       └── observability/
│
├── cli/
│   └── amodex_cli/                 # Termux/client package, created in Stage 1/6 as needed
│
├── migrations/                     # database migrations, created with first schema migration
├── schemas/                        # generated/published JSON Schemas once runtime contracts are executable
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── security/
│   ├── fault/
│   ├── benchmark/
│   └── fixtures/
│
├── deploy/
│   ├── northflank/
│   ├── containers/
│   └── local/
│
├── scripts/                        # only operator/dev scripts with active call sites
├── docs/
│   ├── decisions/
│   ├── contracts/
│   ├── runbooks/
│   └── research/
│
└── research/                       # disposable PoC source only when a PoC actually needs code
```

The mature tree is intentionally compact. New top-level roots require an ADR unless they are conventional tool metadata such as `.github/`.

## 3. Language and packaging baseline

The initial control-plane implementation should use Python unless a Stage 0 PoC identifies a blocking incompatibility.

Reasons:

- Hermes Agent is Python-oriented and can be embedded/wrapped without an unnecessary language bridge;
- FastAPI/Pydantic/PostgreSQL tooling is mature;
- orchestration, provider adapters, background jobs, schema validation, and test tooling are well supported;
- Serena integration and subprocess/MCP orchestration do not require a Node control plane;
- Godot MCP can remain a Node process local to the Runner rather than forcing the platform core into TypeScript.

The Termux CLI may initially be Python as well so one contract/model package can be reused. A later thin native/mobile UI is independent of this choice.

## 4. `src/amodex/contracts/`

Purpose: executable representations of `docs/contracts/RUNTIME_CONTRACTS.md`.

Owns:

- versioned authority-bearing models;
- enum/state definitions;
- validation rules;
- schema serialization;
- migration boundaries for contract versions.

Must not own:

- database queries;
- provider-specific behavior;
- business orchestration;
- Git calls;
- HTTP transport.

Invariant: there is one canonical model for each runtime authority object. API, storage, workers, and CLI do not define shadow versions.

## 5. `src/amodex/control_plane/`

Purpose: deterministic authority and orchestration.

Expected responsibilities:

- task/run state transitions;
- DAG readiness;
- lease acquisition/renewal/expiry;
- path ownership;
- scheduler;
- approval and policy gates;
- correction scheduling;
- recovery coordination;
- promotion orchestration.

This package must not contain a hidden general-purpose autonomous agent loop. LLM reasoning belongs behind explicit Director/Worker interfaces.

Suggested internal modules only when real code exists:

```text
control_plane/
├── orchestrator.py
├── scheduler.py
├── state_machine.py
├── leases.py
├── ownership.py
├── recovery.py
└── policy.py
```

These names are not permission to pre-create empty files.

## 6. `src/amodex/director/`

Purpose: Technical Director reasoning boundary.

Owns:

- structured planning requests/responses;
- task decomposition;
- capability requirement proposals;
- context requirement proposals;
- architecture-change requests;
- correction/escalation strategy;
- plan validation before deterministic admission.

It does not own leases, provider quotas, Git mutation, or promotion authority.

The Director communicates through versioned contracts. It never imports database internals to mutate task state directly.

## 7. `src/amodex/runtime/`

Purpose: agent-runtime integration, initially including the Hermes adapter if PoCs validate it.

Owns:

- worker invocation lifecycle;
- Hermes adapter/facade;
- tool exposure filtering;
- worker callback/event conversion;
- interruption/cancellation mapping;
- worker report collection;
- context delivery to agent runtime.

Amodex-specific governance must remain outside Hermes internals whenever practical. If a Hermes fork becomes necessary, it must be isolated behind this package and justified by ADR.

## 8. `src/amodex/providers/`

Purpose: provider-neutral model access and Provider Governor.

Expected sub-boundaries:

```text
providers/
├── governor/
├── adapters/
├── routing/
└── capability_registry/
```

Owns:

- provider/model capability profiles;
- free/paid eligibility;
- quota state;
- token/concurrency admission;
- normalized errors;
- retry/backoff/circuit-breaker state;
- fallback equivalence;
- usage observations.

Workers never hold raw provider policy logic.

## 9. `src/amodex/storage/`

Purpose: durable persistence abstraction and PostgreSQL implementation.

Owns:

- transaction boundaries;
- repositories/queries;
- event append;
- current-state persistence;
- job claiming;
- database-level concurrency primitives;
- migration execution integration.

Business rules remain in control-plane/domain services, not buried in SQL repositories.

A future alternative storage backend must not change contracts above this boundary.

## 10. `src/amodex/context_engine/`

Purpose: repository ingestion, semantic graph, authority resolution, and Context Capsule compilation.

Expected responsibilities:

- repository map;
- file/blob identity;
- documentation authority graph;
- ADR/decision index;
- symbol/dependency index adapters;
- context selection/ranking;
- provenance and freshness;
- token-budget compilation;
- prompt-injection boundary labeling.

Serena, Tree-sitter, LSP, Git, or Aider-style repo-map logic are adapters/sources, not the Context Engine's authority model.

## 11. `src/amodex/git/`

Purpose: Git/GitHub execution boundary.

Owns:

- repository clone/worktree lifecycle;
- task branch creation;
- checkpoint commits/push;
- expected-SHA ref operations;
- candidate creation;
- PR projection;
- integration simulation;
- promotion/reconciliation;
- remote-state observations.

GitHub API specifics should live behind an adapter so Git remains usable with other remotes later.

## 12. `src/amodex/runners/`

Purpose: disposable execution backends and profiles.

Expected separation:

```text
runners/
├── manager/
├── backends/
│   ├── local_container/
│   ├── northflank/
│   └── github_actions/
└── profiles/
    ├── general/
    ├── godot_headless/
    └── godot_visual/
```

Only backends that pass their PoCs become production packages.

A Runner profile describes required capabilities. A backend describes how capacity is provisioned. The task layer does not contain Northflank-specific logic.

## 13. `src/amodex/tools/`

Purpose: trusted tool wrappers exposed to worker runtimes.

Owns:

- filesystem canonicalization and scope enforcement;
- shell policy;
- Git task-level commands;
- MCP capability adapters;
- side-effect classification;
- idempotency keys;
- fencing-token checks;
- structured tool results.

Raw Hermes tools are never automatically equivalent to Amodex-authorized tools. Tool exposure passes through this layer or an equivalent enforcement adapter.

## 14. `src/amodex/validation/`

Purpose: deterministic acceptance evidence.

Owns:

- validator registry;
- validator execution contracts;
- environment binding;
- evidence capture;
- artifact hashing;
- scope/hygiene checks;
- post-integration revalidation planning.

A model may request a validator, but only this system produces trusted ValidationEvidence.

## 15. `src/amodex/security/`

Purpose: reusable security controls that do not belong to one transport/backend.

Examples:

- secret redaction;
- command/network policies;
- path/symlink canonicalization primitives;
- trust labels;
- signed/hashed artifact verification;
- capability-risk policy;
- untrusted repository content handling.

Do not build a second authorization system here. Authority decisions remain centralized in policy/control-plane contracts.

## 16. `src/amodex/api/`

Purpose: external control-plane interface.

The API exposes owner/client operations such as:

- create/inspect/pause/resume/cancel run;
- inspect tasks/events/checkpoints/evidence;
- attach to live status stream;
- approve high-impact action;
- configure project/provider references;
- request promotion.

The API does not execute worker logic in request handlers. Long operations become durable jobs/tasks before the client receives acknowledgement.

## 17. `src/amodex/observability/`

Purpose: structured operational visibility without chain-of-thought capture.

Owns:

- event projection;
- traces/metrics integration;
- correlation IDs;
- bounded structured logs;
- secret-safe diagnostic context;
- run/task timeline views.

It must never become a dumping ground for raw prompts or credentials.

## 18. `cli/amodex_cli/`

Purpose: lightweight Termux/client surface.

The CLI is not the runtime authority.

Initial command family:

```text
amodex login/configure
amodex project add
amodex run
amodex status
amodex attach
amodex logs
amodex pause
amodex resume
amodex stop
amodex approve
amodex promote
```

Commands should call the remote API. Closing Termux must not terminate remote work.

## 19. `migrations/`

Created only when the first real PostgreSQL schema exists.

Rules:

- every migration is immutable after release;
- migration ordering is deterministic;
- destructive changes require backup/rollback analysis;
- schema state is tested from clean DB and previous supported version;
- application code never silently self-mutates production schema outside migration authority.

## 20. `schemas/`

Created when executable contract models can emit canonical schemas.

Generated JSON Schemas are build artifacts that may be committed only if they serve contract review, external clients, or compatibility tests and are reproducibly generated.

They must never diverge manually from source contract models.

## 21. `tests/`

Test roots are created when their first real test exists.

Categories:

- `unit/` — pure domain/policy logic;
- `contract/` — schema/API/provider/Runner contract compatibility;
- `integration/` — real PostgreSQL/Git/subprocess/provider simulator paths;
- `security/` — prompt injection, path escape, secret, command/network policy;
- `fault/` — crashes, lost responses, stale leases, partial side effects;
- `benchmark/` — Black-Islands and performance/quality scenarios;
- `fixtures/` — bounded deterministic fixtures shared across tests.

No test is considered meaningful merely because it mocks the entire layer under test.

## 22. `deploy/`

Created when Stage 1 deploys the first real remote vertical slice.

Contains reproducible deployment definitions/configuration, not secrets.

Northflank-specific configuration belongs under `deploy/northflank/`; container definitions under `deploy/containers/`; local development under `deploy/local/`.

The control plane must remain portable even if Northflank is the initial host.

## 23. `research/`

`research/` is the only acceptable home for disposable PoC implementation.

Every PoC directory must contain a conclusion record with:

- hypothesis;
- environment/version;
- commands/test procedure;
- observed result;
- measured resource use where relevant;
- decision: `ADOPT`, `ADAPT`, `REJECT`, or `RETEST`;
- cleanup/promotion destination.

Research code cannot be imported by production packages.

## 24. `.github/`

When CI starts, this conventional root may contain:

- workflows;
- issue/PR templates only if actively used;
- dependency/security automation config.

Workflow YAML must call real test/build commands defined by the repository. Do not embed a second orchestration architecture inside CI YAML.

## 25. Dependency-direction rules

Preferred high-level dependency direction:

```text
API / CLI
   |
   v
Control Plane ---------> Director interface
   |                         |
   |                         v
   |                    Agent Runtime
   |
   +--> Contracts
   +--> Storage
   +--> Provider Governor
   +--> Context Engine
   +--> Git
   +--> Runner Manager
   +--> Validation
   +--> Security policy
```

Forbidden dependency inversions include:

- contracts importing storage/API;
- provider adapters importing task scheduler internals;
- Runner backends deciding promotion policy;
- Hermes adapter mutating database state directly;
- validators granting worker scope;
- Git adapter choosing which architecture decision is accepted.

## 26. Repository hygiene baseline

The implementation repository should eventually enforce:

- one formatter/linter configuration per language;
- one dependency lock strategy per package ecosystem;
- no `.env` committed;
- no local SQLite/PostgreSQL dump committed;
- no virtualenv/node_modules/import cache committed;
- no screenshots/logs in source roots;
- no generated Godot `.godot/` cache in Amodex repo;
- no provider secrets in tests; use simulators/fake local endpoints;
- no unused script older than the stage that created it without an owner/call site;
- no duplicated contract definitions.

## 27. Architectural ownership map

At mature V1:

| Domain | Primary authority | May not decide |
|---|---|---|
| contracts | contracts package | scheduling/policy |
| task lifecycle | control plane | model output truth |
| reasoning plans | director | permissions |
| agent loop | runtime/Hermes adapter | Git promotion |
| provider admission | Provider Governor | task scope |
| repository understanding | Context Engine | security override |
| Git operations | Git service | architecture authority |
| execution environment | Runner Manager | task acceptance |
| evidence | Validation Engine | scope grants |
| secrets/trust primitives | Security | business completion |
| client transport | API/CLI | durable authority |

## 28. Repository-layout acceptance gate

Before implementation Stage 1 begins, this layout is accepted as a dependency map, not pre-created as files.

During implementation, a new module/package is accepted only when:

1. it owns a responsibility defined here or in an ADR;
2. it has a real caller or external entry point;
3. it has at least one executable test at the appropriate boundary;
4. it does not duplicate an existing authority;
5. its dependencies follow the allowed direction;
6. it is included in the same vertical slice that proves its integration.

This rule is the primary defense against a repository that looks architecturally impressive while containing disconnected scaffolding.