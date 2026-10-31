# P0-04 — PostgreSQL Job Claim, Lease, and Fencing

Status: **IN_PROGRESS**

P0-04 is a blocking Stage 0 experiment. It tests whether PostgreSQL alone can provide the initial durable coordination required by Amodex without Redis.

## Hypothesis

A real PostgreSQL instance can safely provide:

- single-owner job claiming;
- transactional lease heartbeat;
- lease expiry and reassignment;
- monotonically increasing fencing tokens;
- rejection of stale-worker writes;
- atomic state transition + event append;
- rollback after process death during an uncommitted transition;
- deterministic recovery after the killed worker's lease expires;
- acceptable baseline connection/resource behavior for the next hosting PoCs.

## Probe implementation

`probe.py` uses multiple OS processes against one real PostgreSQL instance.

The test schema contains only two research tables:

```text
jobs
events
```

No production persistence package is introduced by this PoC.

## Required scenarios

### 1. Concurrent claim race

Four worker processes begin together and attempt to claim the same queued job using:

```sql
FOR UPDATE SKIP LOCKED
```

Expected:

```text
workers: 4
active winners: 1
```

### 2. Transactional heartbeat

The current worker extends its lease only when all authority fields still match:

```text
job_id
lease_owner
fencing_token
state=RUNNING
lease_not_expired
```

The lease update and `HEARTBEAT` event append happen in the same transaction.

### 3. Lease expiry and reassignment

The test expires the first lease, reclaims the same job with another worker, and requires:

```text
new_fencing_token = old_fencing_token + 1
```

### 4. Stale-worker rejection

After reassignment, the old worker attempts a protected update with the old fencing token.

Expected:

```text
rows updated: 0
```

The current lease holder must still be able to update the protected state.

### 5. State + event atomic commit/rollback

One transition commits a state change and event together.

A second transition intentionally raises after both SQL mutations but before commit.

Expected after rollback:

```text
state change persisted: false
event persisted: false
```

### 6. Process kill during transaction

A child process:

1. opens a transaction;
2. mutates job state;
3. appends an event;
4. signals the parent while still uncommitted;
5. is hard-killed by the parent.

Expected PostgreSQL behavior:

```text
uncommitted state mutation: rolled back
uncommitted event: rolled back
```

The parent then expires the abandoned lease and requires another worker to reclaim the job with a higher fencing token.

### 7. Advisory-lock dependence

The PoC does not use advisory locks. The final observation queries `pg_locks` and requires zero advisory locks during the finished probe state.

## Security/fault probe

This PoC satisfies the cross-PoC negative test:

```text
stale worker uses old fencing token
```

The stale worker must be unable to mutate protected state after reassignment.

## Resource observations

The probe records:

- PostgreSQL server version;
- `shared_buffers`;
- `work_mem`;
- `maintenance_work_mem`;
- `max_connections`;
- observed active database connections;
- database size after the experiment;
- advisory lock count.

The workflow also records container memory/CPU from the CI service container where available.

This CI measurement is a coordination/runtime proxy only. The intended free-host memory envelope is re-measured on the actual Northflank Control Plane/database PoC (P0-06).

## Pass criteria

P0-04 can be `ADOPT` only if all of these are demonstrated against real PostgreSQL:

```text
exactly one concurrent claim winner
heartbeat transaction succeeds
expired lease is reclaimable
fencing token increments
stale worker write is rejected
current worker write succeeds
state+event commit is atomic
state+event rollback is atomic
process-kill transaction rolls back
killed job is recoverable after expiry
no advisory-lock dependency
```

If coordination is correct but resource-envelope evidence is insufficient for the target host, the decision may be `ADAPT` with resource sizing explicitly deferred to P0-06 rather than pretending it was measured on Northflank.

## Current decision

**IN_PROGRESS.**

The probe is implemented. Closure requires a successful CI run against the real PostgreSQL service plus recorded resource observations.
