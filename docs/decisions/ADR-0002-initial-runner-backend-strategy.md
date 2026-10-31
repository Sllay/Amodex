# ADR-0002 — Initial RunnerBackend strategy after Stage 0

Status: **Accepted**

Date: 2026-10-31

## Context

The architecture closed before implementation assumed Northflank jobs were the preferred initial on-demand Runner backend, with portability/fallback retained as a requirement if hosting constraints invalidated that choice.

Stage 0 produced two relevant facts:

1. the owner's current Northflank account cannot create another free project because its single free project slot is already occupied by an unrelated project that must remain untouched;
2. GitHub Actions standard hosted runners successfully executed the complete disposable Runner lifecycle, external cancellation/reconstruction, Godot 4.7.2 headless toolchain, and Godot visual/Xvfb/MCP validation in the public Amodex repository.

GitHub Actions is not unlimited free compute for private repositories. Black-Islands is private, so the Stage 0 evidence cannot be generalized into “all Amodex Runner work is free on Actions.”

The normative zero-cost policy forbids converting a free-backend limitation into paid usage without explicit owner authorization.

## Decision

Amodex keeps **RunnerBackend as a backend-neutral contract**.

The initial accepted strategy is:

```text
RunnerBackend
    |
    +-- GitHubActionsRunnerBackend
    |     accepted for work that the cost/eligibility policy classifies as free
    |
    +-- future verified-free RunnerBackend(s)
          required when workload/repository/account constraints make Actions ineligible
```

GitHub Actions is accepted as the first concrete disposable RunnerBackend candidate for public-repository Stage 0/Stage 1 validation and other jobs whose current account/repository allowance is explicitly classified as zero-cost.

It is **not** designated the universal permanent Runner backend.

The Control Plane/Task layer must not contain GitHub-Actions-specific lifecycle assumptions. Backend-specific dispatch, job IDs, cancellation APIs, logs/artifact retrieval, and allowance checks belong behind the RunnerBackend adapter/configuration boundary.

## Required zero-cost admission rule

Before a task is dispatched to a Runner backend, Amodex must be able to classify that backend for the task/account/repository as one of at least:

```text
ELIGIBLE_FREE
WAITING_FREE_CAPACITY
INELIGIBLE_COST
UNAVAILABLE
```

`INELIGIBLE_COST` may never silently transition into paid execution.

If no backend is currently `ELIGIBLE_FREE`, the task waits/blocks or uses another verified-free backend.

## Evidence

### P0-07 General Runner

`research/p0-07-general-runner/RESULT.md`

Proved on GitHub hosted runners:

- fresh disposable environment;
- task/runner identity;
- exact checkout;
- deterministic execution;
- heartbeat;
- remote SAFE checkpoint;
- reconstruction on a second VM;
- independent controller-driven cancellation;
- checkpoint survival after cancellation;
- cleanup of temporary refs.

External cancel proof:

```text
target run:     41197666081
controller run: 41197666080
source SHA:     aa25cc97c90f7faa1dbd196debc649ec12aa9df7
```

### P0-08 Godot headless

`research/p0-08-godot-headless/RESULT.md`

Proved exact Black-Islands-like pins for Godot 4.7.2, Node 22.23.4, Godot MCP 0.1.2 and Serena 2.1.0, including LSP, parser/import, real runtime boot, focused headless test and addon-backed MCP headless scene backend.

### P0-09 Godot visual

`research/p0-09-godot-visual/RESULT.md`

Proved Xvfb/Mesa llvmpipe visual execution, live MCP runtime inspection, native input, before/after screenshot evidence, performance observations, repeatability and strict process/display cleanup.

### Northflank limitation

`research/p0-06-northflank/RESULT.md`

Northflank token/API were valid, but the current account returned `409 Maximum number of free projects reached`. The existing unrelated project is explicitly protected and was never reused.

## P0-10 / portability status

This ADR does **not** claim Runner portability between two concrete production backends.

`docs/POC_REGISTER.md` defines P0-10 as blocking **before claiming Runner portability** and permits it during Stage 1/2.

Therefore:

- Stage 0 may close without P0-10;
- Amodex must not advertise verified multi-backend Runner portability yet;
- P0-10 becomes mandatory before that portability claim or before a Stage 1/2 requirement actually depends on a second backend.

## Alternatives considered

### Reuse the existing Northflank project

Rejected. It is unrelated owner infrastructure and explicitly protected from Amodex experiments.

### Create another Northflank account

Unavailable to the owner during Stage 0. Not treated as a technical solution.

### Treat GitHub Actions as universally free

Rejected. Private-repository/account allowances are bounded and current billing eligibility must be checked at dispatch time.

### Delay all Runner work until a second provider is found

Rejected for Stage 0. The Stage 0 blocking questions—disposable lifecycle, cancellation/recovery, Godot headless, and visual feasibility—were already answered by a real zero-cost eligible backend. P0-10 specifically permits later portability proof.

## Consequences

Positive:

- Stage 1 can proceed without coupling task contracts to a hosting vendor;
- the proven public-repository backend remains immediately useful;
- zero-cost policy stays fail-closed for private workloads;
- a later backend can be added without changing TaskPackage/RunnerSpec semantics if the adapter boundary is respected.

Costs/limitations:

- Black-Islands private heavy execution does not yet have a proven unlimited-free Runner backend;
- Stage 1 must expose backend eligibility/capacity instead of assuming compute availability;
- P0-10 remains open and must be executed before verified portability is claimed.

## Compatibility / migration impact

No production Runner adapter existed before this ADR, so there is no runtime migration.

Stage 1 implementation must encode the backend-neutral interface and cost admission rule from the beginning rather than introducing GitHub Actions details into task authority contracts.

## Supersedes

No prior ADR.

This ADR narrows/replaces the pre-implementation assumption that Northflank jobs would be the initial Runner implementation for the owner's current environment.
