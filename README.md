# Amodex

Amodex is a persistent, provider-agnostic engineering agent platform designed to execute long-running software-development work under strict technical governance, recoverable state, scoped repository authority, and a zero-cost-by-default operating model.

Amodex is not intended to be a swarm of autonomous agents debating architecture. The system is built around a Technical Director that decomposes work, compiles minimal task context, assigns narrowly-scoped workers, enforces ownership and validation, and promotes only validated Git checkpoints/candidates through deterministic gates.

## Current status

**Architecture/design Parts 1–3 are complete and Stage 0 feasibility is COMPLETE. Stage 1 is next.**

Stage 0 closed on 2026-10-31 with a green final closeout gate. The consolidated evidence is in `research/STAGE0_RESULT.md`.

Final Stage 0 decisions:

```text
P0-01  ADOPT — executable contracts / Python baseline
P0-02  ADAPT — Hermes bounded worker runtime
P0-03  ADOPT — real zero-cost Gemini provider path
P0-04  ADAPT — PostgreSQL claim / lease / fencing
P0-05  ADAPT — Git/GitHub expected-SHA + reconciliation
P0-06A REJECT_AS_PRIMARY_FOR_CURRENT_ACCOUNT — Northflank account capacity
P0-06B ADAPT — Cloudflare Workers + Neon event-driven Control Plane
P0-07  ADAPT — disposable General Runner lifecycle + external cancellation
P0-08  ADAPT — Godot 4.7.2 headless Runner capability
P0-09  ADAPT — Godot windowed/Xvfb/input/screenshot Runner capability
P0-10  DEFERRED — required before claiming verified multi-backend Runner portability
```

Stage 0 proved the high-risk external assumptions with real providers, PostgreSQL, GitHub operations, hosted control-plane infrastructure, disposable runners, Godot 4.7.2, Serena and Godot MCP. It did **not** build the full production scheduler, Director, coding worker or user-facing product.

## Core product constraints

1. **Zero-cost default.** Normal operation must remain at R$0. Paid inference, paid compute, paid storage, and paid fallbacks are disabled unless the owner explicitly opts in.
2. **Remote persistence.** Closing Termux or losing the client connection must not cancel durable work.
3. **Recoverable execution.** Runs are state machines, not chat transcripts. A worker crash must be recoverable from durable state and Git checkpoints.
4. **One authority chain.** User/owner -> Amodex Technical Director -> Orchestrator -> scoped workers -> deterministic validators.
5. **No free-form agent politics.** Workers do not debate each other, reopen accepted decisions, or change architecture because another model prefers a different style.
6. **One writer per path.** Concurrent writers for the same file or protected module are forbidden by the tool layer, not merely by prompt instruction.
7. **Selective context.** Workers receive task-specific Context Capsules rather than the whole repository or whole project history.
8. **Git is part of execution authority.** Workers never push directly to `main`. Checkpoints, validation, Candidates, Promotion, stale-base detection, and rollback are first-class system behavior.
9. **Deterministic control plane.** Scheduling, leases, permissions, provider limits, Git gates, validation status, and monetary policy must not depend on an LLM deciding to behave correctly.
10. **Evidence over claims.** A task is complete only when its acceptance criteria have executable evidence bound to the exact code/environment state tested.

## Evidence-backed foundation strategy

### Worker runtime

Hermes Agent by Nous Research is accepted with adaptation as the worker/runtime substrate. Amodex owns durable authority, provider routing, tool authorization, delegation, cancellation, Git policy and cost policy. Hermes runs inside a bounded worker runtime rather than becoming the Control Plane.

### Control Plane

Stage 0 replaced the original Northflank-primary assumption for the current owner account. The accepted baseline is:

```text
Cloudflare Workers
  -> thin stateless/event-driven Control Plane surface

Neon PostgreSQL
  -> durable authority/state

PostgreSQL
  -> initial scheduler/lease/fencing coordination substrate
```

Cloudflare Cron is accepted only for periodic reconciliation/maintenance wakeups because real propagation was measured in minutes. Cloudflare Queues were not proven in Stage 0 and are not implicitly adopted.

See `docs/decisions/ADR-0001-event-driven-control-plane-hosting.md`.

### Runner strategy

`RunnerBackend` remains backend-neutral. GitHub Actions is the first concrete disposable backend only when the current repository/account policy classifies that execution as free. It is not treated as unlimited free compute for private repositories.

P0-10 remains open and must run before Amodex claims verified portability across two concrete Runner backends.

See `docs/decisions/ADR-0002-initial-runner-backend-strategy.md`.

## Planned implementation stages

Implementation is divided into seven stages:

| Stage | Scope | Status / exit meaning |
|---|---|---|
| 0 | Executable contracts + feasibility PoCs | **COMPLETE** — high-risk assumptions measured |
| 1 | Durable remote runtime + provider layer | **NEXT** — remote run survives client disconnects/restarts |
| 2 | Scoped coding worker + deterministic tools | first useful single-worker coding vertical slice |
| 3 | Technical Director + DAG + Context Engine + ownership | orchestrated engineering system |
| 4 | Git checkpoints + validation + Candidate/Promotion | **Amodex V1 Safe** for carefully scoped real projects |
| 5 | Specialized workers and capability routing | controlled specialization without swarm behavior |
| 6 | Product surfaces: Termux CLI, web, project operations | daily-use product rather than engineering prototype |

The detailed dependency order, entry gates, vertical slices, and exit gates live in `docs/IMPLEMENTATION_PROGRAM.md`.

## Documentation map

### Part 1 — Foundation and authority model — COMPLETE

- `AGENTS.md`
- `docs/PROJECT_CHARTER.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/ZERO_COST_AND_HOSTING.md`
- `docs/AGENT_GOVERNANCE.md`
- `docs/CONTEXT_ENGINE.md`
- `docs/GIT_LIFECYCLE.md`
- `docs/research/HERMES_BASELINE.md`

### Part 2 — Runtime contracts and integration specifications — COMPLETE

- `docs/contracts/RUNTIME_CONTRACTS.md`
- `docs/PROVIDER_GOVERNOR.md`
- `docs/PERSISTENCE_AND_RECOVERY.md`
- `docs/SECURITY_THREAT_MODEL.md`
- `docs/RUNNER_AND_MCP_PROTOCOL.md`
- `docs/GITHUB_INTEGRATION.md`
- `docs/research/PART2_VERIFIED_BASELINE_2026-10-31.md`

### Part 3 — Implementation program and acceptance system — COMPLETE

- `docs/REPOSITORY_LAYOUT.md`
- `docs/IMPLEMENTATION_PROGRAM.md`
- `docs/POC_REGISTER.md`
- `docs/TEST_AND_FAULT_MATRIX.md`
- `docs/BLACK_ISLANDS_BENCHMARK.md`
- `docs/DEPLOYMENT_RUNBOOK.md`
- `docs/DEFINITION_OF_DONE.md`
- `docs/KICKOFF_PLAN.md`

### Stage 0 implementation evidence — COMPLETE

- `research/STAGE0_RESULT.md` — consolidated closeout.
- `research/p0-01-contracts/RESULT.md`
- `research/p0-02-hermes/RESULT.md`
- `research/p0-03-providers/RESULT.md`
- `research/p0-04-postgres/RESULT.md`
- `research/p0-05-git/RESULT.md`
- `research/p0-06-northflank/RESULT.md`
- `research/p0-06-cloudflare-neon/RESULT.md`
- `research/p0-07-general-runner/RESULT.md`
- `research/p0-08-godot-headless/RESULT.md`
- `research/p0-09-godot-visual/RESULT.md`
- `docs/decisions/ADR-0001-event-driven-control-plane-hosting.md`
- `docs/decisions/ADR-0002-initial-runner-backend-strategy.md`

## Reference project

`Sllay/Black-Islands` is the primary real-world benchmark for Amodex. It already demonstrates progressive disclosure, scoped subagents, Godot MCP, Serena, phase contracts, build evidence, ADRs, validated checkpoints, and a large modular Godot 4.7.2 project.

Black-Islands remained read-only during Stage 0. No write benchmark was authorized or performed.

## Immediate next step — Stage 1

The next normative vertical slice is **Stage 1 — Durable remote runtime and provider layer**:

```text
client
 -> remote API
 -> durable Run/Task row
 -> scheduler claims work
 -> worker attempt lease
 -> zero-cost Provider Governor admits an eligible free provider
 -> real model request
 -> normalized durable result/event
 -> run completes
 -> disconnected client reconnects and reads final state
```

Stage 1 must additionally prove control-plane restart recovery, worker-crash recovery, clean database migrations, malformed-provider protection, secret-safe events/logs, and free-quota exhaustion that waits/blocks instead of spending.

Do not start Stage 2 coding-agent capabilities until the Stage 1 exit gate is green.

## License

Amodex is currently **proprietary / source-visible**, not open source. Public visibility does not grant permission to copy, modify, redistribute, train on, or derive competing products from the repository. See `LICENSE.md`.
