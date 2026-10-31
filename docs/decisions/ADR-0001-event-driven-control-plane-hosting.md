# ADR-0001 — Event-driven Control Plane hosting with Cloudflare Workers + Neon Postgres

Status: **Accepted for Stage 1 baseline**

Date: 2026-10-31

Decision authority: Stage 0 feasibility evidence

Supersedes: the unproven assumption that Northflank would necessarily be the primary free Control Plane host for this owner/account.

Does **not** reject Northflank as a technology generally.

---

## Context

Amodex requires a zero-cost-by-default Control Plane with durable task/run authority, provider governance, Git/GitHub coordination, reconciliation and remote Runner orchestration.

The architecture originally preferred a small always-on Northflank service plus PostgreSQL because the then-current Sandbox offering appeared to fit the zero-cost requirement.

P0-06A reached the real Northflank API with a valid account token but project creation returned:

```text
HTTP 409 Conflict
Maximum number of free projects reached
```

The account's existing Northflank project is unrelated and explicitly protected. It must not be reused, modified, renamed or deleted for Amodex. Creating a separate Northflank account was not available to the owner.

The hosting abstraction already allowed a different backend without changing task authority or Runner contracts, so Stage 0 evaluated Cloudflare Workers + Neon Postgres as P0-06B.

---

## Decision

Use the following as the Stage 1 Control Plane hosting baseline:

```text
Cloudflare Workers
  -> thin HTTP/API Control Plane entrypoints
  -> webhook/event handling
  -> provider-governor entrypoints that fit request budgets
  -> periodic scheduler/reconciliation wakeups

Neon Postgres
  -> durable Run/Task/Event/Lease/Decision authority
  -> PostgreSQL coordination primitives proven by P0-04

RunnerBackend
  -> all long-running or process-oriented execution
  -> Hermes worker runtime
  -> shell/filesystem/Git worktrees
  -> Godot/Serena/MCP execution
```

The Control Plane becomes **event-driven and reconstructable**, not dependent on one continuously alive process.

---

## Authority rules

The hosting change does not change Amodex authority.

1. PostgreSQL remains authoritative for durable coordination/state.
2. A Worker request, isolate, process-local value or cache is never durable truth.
3. A Worker may only act within the same policy/contract boundaries defined for the Control Plane.
4. Long-running work is represented durably and handed to a RunnerBackend.
5. Client disconnect must not cancel durable work unless an explicit cancel transition is authorized.
6. Runner loss and Worker loss are recoverable from PostgreSQL + Git checkpoints/evidence.
7. Zero-cost policy remains fail-closed; no host may silently upgrade to paid capacity.

---

## Scheduler semantics

Cloudflare Cron is accepted for:

- periodic reconciliation;
- expired lease scans;
- provider cooldown/retry wakeups where minute-scale latency is acceptable;
- orphan/resource audits;
- scheduled maintenance tasks.

Cloudflare Cron is **not** the low-latency task queue.

P0-06B observed a real first-trigger propagation delay of:

```text
546.292 seconds
```

Cloudflare currently documents that Cron Trigger configuration changes may take up to 15 minutes to propagate globally. Therefore Stage 1 must not rely on newly configured Cron schedules for immediate dispatch.

Low-latency work activation must come from explicit HTTP/webhook/API events or a separately validated queue/runner polling mechanism.

---

## Queue decision

Cloudflare Queues are **not adopted by this ADR**.

P0-06B did not test enqueue/dequeue semantics. P0-04 already proved PostgreSQL can provide the initial claim/lease/fencing coordination without Redis.

Therefore:

```text
Stage 1 authoritative coordination = PostgreSQL
Cloudflare Queues = optional future optimization only
```

Any later adoption requires a dedicated PoC/ADR and must preserve PostgreSQL authority or explicitly supersede it.

---

## Evidence

### P0-04 PostgreSQL coordination

Proved:

- concurrent `SKIP LOCKED` claims;
- transactional heartbeats;
- lease expiry/reclaim;
- monotonically increasing fencing tokens;
- stale worker rejection;
- atomic state + event transitions;
- hard-kill rollback/recovery.

### P0-06B real hosted path

Successful workflow:

```text
run: 41189477201
job: 124503095078
source SHA: 80e16bb0563836d6bfd53ec066fab189446692e8
```

Proved with real Cloudflare Workers and real Neon Postgres:

- account/API authentication;
- temporary Neon project creation;
- temporary Worker deployment;
- runtime secret injection;
- PostgreSQL connectivity;
- outbound provider-network connectivity;
- durable event write/read;
- Worker redeploy to a distinct build;
- original durable event surviving redeploy;
- Cron schedule present before and after redeploy;
- real Cron execution without a connected client;
- scheduled handler persisting state to Postgres;
- tracked-secret leakage scan;
- namespaced cleanup.

Pinned research fixture versions:

```text
wrangler 4.156.0
@neondatabase/serverless 1.2.0
```

### Cleanup/reconciliation evidence

A failed earlier P0-06B attempt left research resources behind, demonstrating that best-effort `finally` cleanup is insufficient after ambiguous cloud failures.

Dedicated audit:

```text
run: 41190356342
job: 124505718439
```

Result:

```yaml
cloudflare:
  found: 2
  deleted: 2
  remaining: 0
neon:
  found: 2
  deleted: 2
  remaining: 0
non_prefixed_resources_touched: false
```

This creates an additional requirement: cloud resources must be namespaced, idempotently reconciled and audited after ambiguous outcomes.

---

## Consequences

### Positive

- does not require a second Northflank account or touching unrelated resources;
- preserves the zero-cost baseline with currently free-eligible services;
- durable truth remains normal PostgreSQL;
- HTTP Control Plane survives platform isolate/process replacement naturally;
- scheduled reconciliation works with no client attached;
- Runner separation becomes sharper rather than weaker;
- infrastructure can be recreated from code/config instead of relying on a persistent VM filesystem.

### Costs / constraints

- Worker request CPU/runtime limits require a deliberately thin Control Plane;
- direct in-process background loops cannot be authoritative;
- Cron configuration is eventually propagated and unsuitable for immediate dispatch;
- long-running work must always use RunnerBackend;
- cloud create/delete operations require explicit reconciliation and orphan audits;
- external free-tier limits remain mutable and must be revalidated.

---

## Rejected alternatives for this decision

### Reuse the owner's existing Northflank project

Rejected. The project is unrelated and explicitly protected.

### Delete/rename existing Northflank resources to free a project slot

Rejected. Amodex may not damage unrelated infrastructure to satisfy its own hosting requirement.

### Create another Northflank account

Unavailable to the owner at decision time and not a sound architectural prerequisite.

### Put Hermes/Git/Godot inside Cloudflare Workers

Rejected. Workers are the Control Plane surface, not the execution Runner.

### Adopt Cloudflare Queues without testing

Rejected. No capability is promoted to architecture merely because the provider advertises it.

---

## Revisit conditions

This ADR should be revisited if any of the following occurs:

- Cloudflare/Neon free eligibility materially changes;
- Worker limits prevent the Stage 1 Control Plane vertical slice;
- PostgreSQL coordination fails under measured Stage 1 load;
- a better zero-cost always-on host passes the same authority/recovery gates;
- a validated queue backend materially improves correctness/latency without violating zero-cost policy;
- security or data-governance requirements make either current service unsuitable.

A future replacement must preserve the Part 2 contracts and RunnerBackend separation; changing hosting alone must not rewrite task authority.
