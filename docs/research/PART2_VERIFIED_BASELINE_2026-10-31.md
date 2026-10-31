# Part 2 Verified External Baseline — 2026-10-31

Status: **Research evidence snapshot**

Purpose: record which external capabilities/limits were actually verified while closing Part 2 so future implementation can distinguish architectural decisions from facts that may change over time.

This file is not a promise that any external service will keep the same behavior indefinitely. Before implementation begins, anything listed as externally mutable must be rechecked.

---

## 1. Hermes Agent

Primary source reviewed:

- NousResearch/hermes-agent architecture documentation on GitHub
- current release stream visible on GitHub as of 2026-10-31

Observed current architecture/features:

- multiple entry points including CLI, gateway, API/server-style paths, ACP/editor integration and Python-library-style usage;
- central `AIAgent`/conversation loop handling provider calls, tool calls, retries/fallback/compression/persistence;
- provider runtime resolution shared across entry points;
- tool registry with broad built-in toolsets;
- dynamic MCP integration;
- plugin system with project/user/pip discovery paths;
- persistent session storage using SQLite/FTS5 for Hermes' own session domain;
- terminal backend abstraction;
- context-engine and memory-provider plugin extension points;
- ability to start from a very restricted/blank-slate tool configuration;
- delegation/subagent capability exists but is optional and can be disabled.

Verified implementation direction for Amodex:

- reuse Hermes as worker runtime substrate where integration PoC passes;
- do not use Hermes session DB as authoritative Amodex task/recovery state;
- do not let Hermes internal delegation become global orchestration authority;
- do not let Hermes internal provider fallback bypass Amodex Provider Governor;
- prefer restricted toolsets and explicit MCP filtering.

Current release observed during research:

- Hermes Agent v0.22.0 release dated 2026-10-13 was visible in the upstream GitHub release stream during this architecture pass.

Implementation revalidation required:

- exact embedding/library API to instantiate an agent without CLI assumptions;
- exact hooks needed to intercept provider requests before dispatch;
- exact hooks needed to enforce Amodex capability grants around tool dispatch;
- behavior of MCP tool filtering at runtime;
- stable shutdown/interrupt semantics;
- compatibility/licensing of any copied vs imported internal module.

PoCs that must settle this:

- HERMES-P01: embedded single worker with Amodex-owned task state;
- HERMES-P02: external Governor controls every model request;
- HERMES-P03: delegation disabled and cannot be re-enabled by task prompt;
- HERMES-P04: scoped MCP/toolset exposure;
- HERMES-P05: worker process killed/recreated while task resumes from Amodex state.

Primary source URL:

- https://github.com/NousResearch/hermes-agent

---

## 2. Northflank Sandbox

Primary source reviewed:

- Northflank pricing/application documentation on 2026-10-31.

Observed current free Sandbox limits:

- 2 services;
- 2 jobs;
- 1 database/addon;
- up to $20/month of resources on Northflank Cloud under Sandbox terms;
- always-on/no sleeping stated for Sandbox services;
- compute explicitly described as limited;
- Northflank itself describes free Sandbox as suitable for exploration/hobby/testing rather than production SLA workloads.

Architectural consequence:

```text
SERVICE 1 -> Amodex Control Plane/API
SERVICE 2 -> reserved/optional auxiliary service
ADDON 1   -> PostgreSQL
JOB 1     -> General Runner
JOB 2     -> Heavy/Visual Runner
```

This is a deployment mapping, not an architectural dependency.

Important unresolved fact:

The documentation reviewed does not by itself prove that the free compute envelope can comfortably execute:

- Godot 4.7.2 editor/runtime;
- project dependencies such as Voxel Tools;
- Godot MCP;
- Serena/GDScript LSP;
- Xvfb;
- software rendering;
- screenshot/runtime validation;
- Hermes worker;

inside one job at acceptable memory/CPU usage.

Therefore Godot-on-Northflank remains a PoC question, not a claimed capability.

Mandatory PoC:

- POC-INFRA-001 defined in `docs/RUNNER_AND_MCP_PROTOCOL.md`.

Primary source URLs:

- https://northflank.com/pricing
- https://northflank.com/docs/v1/application/billing/pricing-on-northflank

---

## 3. Godot MCP (`shin2344234/godot-mcp`)

Primary source reviewed:

- upstream GitHub repository current documentation on 2026-10-31.

Observed requirements/capabilities:

- Godot 4.7 or newer;
- upstream documentation explicitly states tested with Godot 4.7.2;
- Node.js 22 or newer for normal server usage;
- scene/node/signal/resource editing;
- GDScript LSP diagnostics/symbols/renames;
- live game runtime control;
- input injection;
- pause/step;
- screenshots;
- profiling;
- record/replay input;
- debugger/DAP-oriented capabilities;
- GUT/GdUnit4 test execution;
- live editor bridge with headless fallback when editor is closed.

Architectural consequence:

Amodex does not need to invent its own Godot editor/runtime automation protocol before first integration. It can treat the MCP server as the project-aware Godot capability provider, while keeping authority/filtering/recovery outside the MCP server.

Security consequence:

- MCP server/editor bridge stays runner-local;
- arbitrary discovered tools are not automatically granted;
- project must pin/approve server version;
- arbitrary runtime eval remains disabled unless an explicit task/security profile permits it.

Primary source URL:

- https://github.com/shin2344234/godot-mcp

---

## 4. Black-Islands project-local baseline

Verified earlier from `Sllay/Black-Islands` and carried into Part 2 as a benchmark contract:

- Godot project root: `game/`;
- target engine: Godot 4.7.2;
- Godot MCP pin: `@shin2344234/godot-mcp@0.1.2` in the project documentation baseline inspected;
- Serena pin: `serena-agent==2.1.0`;
- Voxel Tools route: 1.7/v1.7x in the inspected project tooling baseline;
- project-local Codex MCP config contains Godot and Serena wrappers;
- `AGENTS.md` requires progressive disclosure and scoped subagents;
- `main` represents validated work;
- phase/build logs contain real validation evidence and limitations.

Amodex must not assume those versions are globally correct for every Godot repository. It ingests project pins.

---

## 5. GitHub integration assumptions

Verified design direction:

- GitHub App installation credentials are preferable to long-lived broad personal tokens for repository-scoped automation;
- short-lived installation token model aligns with Amodex least-privilege design;
- GitHub API supports repository/contents/refs/PR/merge/check/action workflows needed by the planned integration, subject to app permissions and repository settings;
- branch protection/ruleset visibility/administration may require permissions not granted to a normal managed integration.

Implementation rule:

Exact GitHub App permission names and event subscriptions are intentionally **not frozen from memory** in Part 2. They must be selected from current GitHub documentation during Stage 0/1 implementation and recorded in deployment configuration.

Reason:

Permission names/API requirements are external and can change. The architecture freezes capability classes, not stale permission spelling.

Primary documentation root:

- https://docs.github.com/en/apps

---

## 6. Provider APIs

Part 2 does not freeze free-tier quotas for Gemini, Groq, NVIDIA or OpenRouter because those values are volatile and may vary by account/model/region/date.

What is frozen:

- provider adapters normalize provider behavior;
- Provider Governor owns rate/cost/fallback admission;
- unknown quota is not infinite;
- runtime headers/account APIs/current config outrank old documentation snapshots;
- zero-cost mode never upgrades to paid silently;
- task enters waiting state when all eligible free capacity is unavailable.

Implementation must create a dated provider capability/quota snapshot at deployment time rather than copying old quota values into source code.

---

## 7. Facts vs architecture decisions

### Externally mutable facts

Must be revalidated before implementation/deploy:

- Hermes exact version/API/hook names;
- Northflank limits/resource plans;
- Godot MCP version/requirements;
- provider free quotas/model IDs;
- GitHub App permission names;
- GitHub Actions included quota for the owner's plan/repository type.

### Architecture decisions

Remain valid unless superseded by ADR:

- zero-cost fail-closed;
- Provider Governor outside worker model;
- disposable runners;
- RunnerBackend portability;
- PostgreSQL as initial durable control plane;
- no Redis requirement for V1;
- GitHub promotion separate from workers;
- MCP local to runner and capability-filtered;
- Godot visual validation is a separate heavier runner profile;
- Black-Islands is the real integration benchmark.

---

## 8. Research integrity rule

Future documentation must not turn an old observation in this file into a timeless claim.

If a future implementation says:

> Northflank allows X

or

> Hermes exposes Y

it should either:

1. cite/recheck current upstream behavior; or
2. explicitly state that it is relying on this dated architecture baseline and is pending revalidation.

This prevents the implementation from hardcoding assumptions that were only true during planning.
