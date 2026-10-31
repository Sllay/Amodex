# Amodex

Amodex is a persistent, provider-agnostic engineering agent platform designed to execute long-running software-development work under strict technical governance, recoverable state, scoped repository authority, and a zero-cost-by-default operating model.

Amodex is not intended to be a swarm of autonomous agents debating architecture. The system is built around a Technical Director that decomposes work, compiles minimal task context, assigns narrowly-scoped workers, enforces ownership and validation, and promotes only validated Git checkpoints.

## Current status

**Architecture and design closure only. Production implementation has not started. Parts 1 and 2 of the three-pass specification are complete; Part 3 remains before implementation begins.**

The repository is intentionally being specified before coding begins. Until the seven implementation stages are formally opened, production code, placeholder services, mock integrations presented as real features, empty modules, speculative dependency wiring, and disconnected scripts are out of scope.

## Core product constraints

1. **Zero-cost default.** Normal operation must remain at R$0. Paid inference, paid compute, paid storage, and paid fallbacks are disabled unless the owner explicitly opts in.
2. **Remote persistence.** Closing Termux or losing the client connection must not cancel durable work.
3. **Recoverable execution.** Runs are state machines, not chat transcripts. A worker crash must be recoverable from durable state and Git checkpoints.
4. **One authority chain.** User/owner -> Amodex Technical Director -> Orchestrator -> scoped workers -> deterministic validators.
5. **No free-form agent politics.** Workers do not debate each other, reopen accepted decisions, or change architecture because another model prefers a different style.
6. **One writer per path.** Concurrent writers for the same file or protected module are forbidden by the tool layer, not merely by prompt instruction.
7. **Selective context.** Workers receive task-specific Context Capsules rather than the whole repository or whole project history.
8. **Git is part of execution authority.** Workers never push directly to `main`. Checkpoints, validation, promotion, stale-base detection, and rollback are first-class system behavior.
9. **Deterministic control plane.** Scheduling, leases, permissions, provider limits, Git gates, validation status, and monetary policy must not depend on an LLM deciding to behave correctly.
10. **Evidence over claims.** A task is complete only when its acceptance criteria have executable evidence.

## Foundation strategy

Hermes Agent by Nous Research is the primary runtime substrate under evaluation because its current architecture already provides a mature agent loop, provider resolution, tool registry, MCP integration, plugin system, persistent sessions, terminal backends, context engines, memory, security controls, and multiple execution surfaces. Amodex will not adopt Hermes' delegation or social-agent behavior blindly. The Amodex Control Plane remains authoritative over task DAGs, file ownership, concurrency, promotion, cost policy, and worker scope.

Northflank Sandbox is the primary zero-cost hosting candidate for the initial control plane. Heavy Godot execution is designed as an on-demand Runner capability rather than a permanently running service. If the free Northflank compute envelope cannot safely run a full Godot visual validation workload, the RunnerBackend abstraction allows a second free backend without changing the control plane.

## Planned implementation stages

Implementation remains divided into seven stages:

| Stage | Scope | Exit meaning |
|---|---|---|
| 0 | Constitution, contracts, threat model, repository rules | system behavior is specified before code |
| 1 | Durable remote runtime + provider layer | remote run survives client disconnects |
| 2 | Scoped coding worker + deterministic tools | first useful single-worker coding vertical slice |
| 3 | Technical Director + DAG + Context Engine + ownership | Amodex becomes an orchestrated engineering system |
| 4 | Git checkpoints + validation + promotion | first safe Amodex V1 suitable for real projects |
| 5 | Specialized workers and capability routing | controlled specialization without swarm behavior |
| 6 | Product surfaces: Termux CLI, web, project operations | daily-use product rather than engineering prototype |

The order can only be changed by an ADR when a dependency or safety requirement proves the current order invalid.

## Documentation map

This repository is being completed in three specification passes before implementation.

### Part 1 — Foundation and authority model — COMPLETE

- `AGENTS.md` — root operating contract for any coding agent or future Amodex instance working on this repository.
- `docs/PROJECT_CHARTER.md` — product scope, invariants, non-goals, authority boundaries, and anti-placeholder rules.
- `docs/SYSTEM_ARCHITECTURE.md` — control plane, runtime, runners, state boundaries, and service topology.
- `docs/ZERO_COST_AND_HOSTING.md` — cost policy, Northflank topology, resource strategy, and fallback rules.
- `docs/AGENT_GOVERNANCE.md` — Technical Director, worker roles, concurrency, ownership, correction flow, and anti-loop policy.
- `docs/CONTEXT_ENGINE.md` — repository ingestion, source authority, context compilation, and progressive disclosure.
- `docs/GIT_LIFECYCLE.md` — branches, worktrees, checkpoints, validation, promotion, stale-base handling, and rollback.
- `docs/research/HERMES_BASELINE.md` — current Hermes capabilities we can reuse and the boundaries Amodex must replace or wrap.

### Part 2 — Runtime contracts and integration specifications — COMPLETE

- `docs/contracts/RUNTIME_CONTRACTS.md` — canonical versioned contracts for projects, runs, tasks, TaskPackages, ContextCapsules, attempts, leases, tools, providers, checkpoints, evidence, promotion, runners, MCP and policy decisions.
- `docs/PROVIDER_GOVERNOR.md` — zero-cost admission, capability routing, quota accounting, rate limiting, retries, circuit breakers, fallback compatibility and provider error normalization.
- `docs/PERSISTENCE_AND_RECOVERY.md` — PostgreSQL authority, event/current-state model, leases/fencing, checkpoint durability, side-effect journaling, crash reconciliation and fault-injection requirements.
- `docs/SECURITY_THREAT_MODEL.md` — trust zones, prompt injection, secrets, filesystem/shell/network isolation, supply chain, MCP trust, evidence integrity and security tests.
- `docs/RUNNER_AND_MCP_PROTOCOL.md` — RunnerBackend lifecycle, profiles, bootstrap, Hermes/MCP integration, Godot/Serena topology, visual/headless validation and hosting PoCs.
- `docs/GITHUB_INTEGRATION.md` — GitHub App strategy, task branch/checkpoint/PR lifecycle, webhooks, polling, protection discovery, Actions integration and expected-SHA promotion behavior.
- `docs/research/PART2_VERIFIED_BASELINE_2026-10-31.md` — dated external fact baseline for Hermes, Northflank, Godot MCP, Black-Islands and implementation revalidation requirements.

### Part 3 — Implementation program and acceptance system — PENDING

Part 3 will freeze the seven-stage implementation DAG, physical repository layout, exact PoCs, test/fault-injection matrix, Black-Islands benchmark, deployment runbook, Definition of Done, and the implementation kickoff sequence.

No implementation stage begins merely because a directory exists. Each stage must satisfy its own entry criteria and vertical-slice exit gate.

## Reference project

`Sllay/Black-Islands` is the primary real-world benchmark for Amodex. It already demonstrates progressive disclosure, scoped subagents, Godot MCP, Serena, phase contracts, build evidence, ADRs, validated checkpoints, and a large modular Godot 4.7.2 project. Amodex must eventually prove that it can continue that style of project without violating its repository contracts or polluting `main`.

## License

Amodex is currently **proprietary / source-visible**, not open source. Public visibility does not grant permission to copy, modify, redistribute, train on, or derive competing products from the repository. See `LICENSE.md`.
