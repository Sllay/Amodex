# P0-04 — PostgreSQL Job Claim, Lease, and Fencing

Status: **ADAPT**

P0-04 proved that PostgreSQL alone can provide the initial durable coordination semantics required by Amodex without introducing Redis. The coordination design is accepted; host-specific resource sizing remains a separate deployment concern to be revalidated on the actual free Control Plane/database environment in P0-06.

## Hypothesis

A real PostgreSQL instance can safely provide:

- single-owner job claiming;
- transactional lease heartbeat;
- lease expiry and reassignment;
- monotonically increasing fencing tokens;
- rejection of stale-worker writes;
- atomic state transition + event append;
- rollback after process death during an uncommitted transition;
- deterministic recovery after the killed worker's lease expires.

The hypothesis passed for coordination semantics.

## Reproducible environment

Clean confirmation run:

```text
workflow: stage0-postgres
run_id: 41183766448
job_id: 124486382734
Amodex commit: 98a92295590921696d756b014d3f4747929b974a
result: success
```

PostgreSQL image was pinned by digest:

```text
postgres@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c
```

Observed server:

```text
PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2)
psycopg 3.2.10
```

Relevant database settings:

```text
shared_buffers: 128MB
work_mem: 4MB
maintenance_work_mem: 64MB
max_connections: 100
```

## Concurrent claim proof

Four independent OS worker processes were released simultaneously against one queued job.

Claim mechanism:

```sql
FOR UPDATE SKIP LOCKED
```

Observed:

```text
workers: 4
winners: 1
first fencing token: 1
```

Exactly one process acquired active authority.

## Transactional heartbeat

The current worker extended its lease only while all authority fields still matched:

```text
job_id
lease_owner
fencing_token
state=RUNNING
lease_not_expired
```

The lease extension and `HEARTBEAT` event append were committed in one transaction.

Observed:

```text
heartbeat_transactional: true
```

## Lease expiry and reassignment

The first lease was deliberately expired and the same job was claimed by a replacement worker.

Observed:

```text
old fencing token: 1
new fencing token: 2
lease_expiry_recoverable: true
```

The fencing token advanced on reassignment.

## Stale-worker negative test

After reassignment, the original worker attempted a protected mutation using its old lease owner/token tuple.

Observed:

```text
stale_worker_mutation_rejected: true
current_worker_mutation_allowed: true
```

This satisfies the Stage 0 cross-PoC security probe requiring an old fencing token to lose authority after reassignment.

## Atomic state + event proof

A normal transition updated job state and appended its event in one transaction.

Observed:

```text
state_and_event_atomic_commit: true
```

A second transition deliberately raised after both SQL mutations but before commit.

Observed after rollback:

```text
state_and_event_atomic_rollback: true
state mutation persisted: false
event persisted: false
```

## Hard process-kill proof

A child worker opened an ordinary psycopg transaction, changed job state, appended an event, signaled the parent while still uncommitted, and was then hard-killed by the parent process.

The clean confirmation run intentionally produced the PostgreSQL log event:

```text
unexpected EOF on client connection with an open transaction
```

There was no duplicate-`BEGIN` warning in the clean rerun.

Observed:

```text
process_kill_rollback: true
uncommitted state mutation persisted: false
uncommitted event persisted: false
```

The abandoned job was then expired and reclaimed by a reconciler:

```text
old fencing token: 1
new fencing token: 2
post-kill recovery: true
```

The new holder could mutate protected state successfully.

## Advisory-lock observation

The coordination probe uses row locking/fencing rather than advisory locks.

Observed:

```text
advisory_lock_count: 0
advisory_locks_used: false
```

No hidden advisory-lock dependency was required for the demonstrated path.

## Resource proxy observation

At the end of the clean GitHub Actions probe:

```text
observed database connections: 1
database size: 7,887,895 bytes
container CPU snapshot: 0.02%
container memory snapshot: 25.36 MiB
container memory percentage on CI host: 0.16%
container PIDs: 6
```

These numbers demonstrate that the PoC itself has a small idle/post-test footprint on the GitHub Actions service container. They are **not** treated as proof of the eventual Northflank free-host envelope because the host limits and service topology differ.

The real deployment/resource decision remains part of P0-06.

## Investigation history

The first successful run (`41183453714`, job `124485444313`) already proved all coordination invariants, but its kill-test worker explicitly issued `BEGIN` on a connection for which psycopg had already opened a transaction. PostgreSQL therefore logged:

```text
WARNING: there is already a transaction in progress
```

Although rollback/recovery still passed, the probe was corrected instead of accepting noisy evidence.

The clean rerun:

- removed the redundant explicit `BEGIN`;
- pinned the exact PostgreSQL image digest;
- retained the intentional hard-kill/open-transaction EOF;
- passed the same original acceptance criteria.

## Decision

**ADAPT**.

Accepted for Amodex architecture:

- PostgreSQL is sufficient for the initial durable claim/lease/fencing authority model;
- Redis is not required for Stage 1 coordination;
- job claims use transactional row locking with `SKIP LOCKED`-style semantics;
- every reassignment advances a fencing token;
- protected mutations must verify current lease ownership/token;
- state transition and event append belong in the same transaction;
- worker death relies on database rollback plus lease expiry/reconciliation, not worker-local truth.

Adaptation retained:

- connection-pool sizing, memory headroom, service limits and production PostgreSQL configuration must be measured on the actual selected free host in P0-06 before deployment assumptions become production constraints.

This is not `RETEST`; the coordination uncertainty is retired and P0-05 may proceed.

## Next gate

```text
P0-05 — Git/GitHub expected-SHA and crash reconciliation
```
