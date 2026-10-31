# P0-06B — Cloudflare Workers + Neon Postgres Control Plane

Status: **ADAPT**

P0-06B replaces Northflank as the current-account primary Control Plane hosting path after P0-06A was rejected for account-capacity reasons. The resulting architecture keeps durable truth in PostgreSQL while allowing the HTTP/scheduler surface to be disposable and event-driven.

## Decision

**ADAPT.**

Adopt:

```text
Cloudflare Worker
  -> thin Control Plane HTTP/API surface
  -> periodic reconciliation/scheduler wakeups
  -> secret injection and provider egress

Neon Postgres
  -> durable authority/state
  -> lease/fencing/coordination substrate already proven by P0-04

RunnerBackend
  -> all long-running execution, Hermes, shell, Git worktrees and Godot
```

Do not adopt:

```text
Cloudflare Worker as a Runner
Cloudflare local memory as durable truth
Cloudflare Cron as low-latency task dispatch
Cloudflare Queues as an authoritative queue in Stage 1
```

Cloudflare Queues were **not tested by this PoC** and are not required for Stage 1. PostgreSQL remains the initial authoritative coordination/queue/lease substrate from P0-04. A free queue may be evaluated later only as an optimization with its own evidence.

---

## Why ADAPT instead of ADOPT

The original hosting assumption expected a small continuously running Control Plane process. Cloudflare Workers instead provide event-driven/serverless execution.

That changes the runtime shape but not the authority model:

```text
client / webhook / scheduled wakeup
            |
            v
     Cloudflare Worker
     thin Control Plane
            |
            v
       Neon Postgres
     durable authority
            |
            +--> Provider Governor / provider APIs
            +--> disposable RunnerBackend
```

Long-lived execution must never depend on a Worker request staying alive. The Worker validates, mutates durable state transactionally, schedules/reconciles work, and returns. Long work executes in Runners.

Cron is accepted for periodic reconciliation/maintenance, not low-latency dispatch, because Cloudflare documents that Cron Trigger configuration changes may take up to 15 minutes to propagate globally. The real PoC observed 546.292 seconds (~9.1 minutes) before the newly deployed trigger produced its first durable event.

---

## Environment and pins

Successful source SHA:

```text
80e16bb0563836d6bfd53ec066fab189446692e8
```

GitHub Actions runner observed:

```text
Ubuntu 26.04.1 LTS
Node.js 24.22.1
npm 11.19.1
```

Fixture pins:

```text
wrangler: 4.156.0
@neondatabase/serverless: 1.2.0
Cloudflare compatibility date: 2026-10-31
Neon PostgreSQL project: pg_version 18
```

No production dependency is inferred solely from these research pins. Stage implementation must preserve intentional pinning and revalidate mutable external constraints.

---

## Credential gate

Required GitHub Actions secrets:

```text
CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID
NEON_API_KEY
```

Optional when an account exposes more than one Neon organization:

```text
NEON_ORG_ID
```

Read-only credential/capability checks passed before mutation:

```text
Cloudflare Workers API access: true
Neon API access: true
credential values recorded: false
mutation during credential gate: false
```

The probe does not print token values, account ID, Neon API key, database URI or runtime secret.

---

## Real deployment procedure

The successful mutation probe performed all of the following against the real free accounts:

1. resolved the Neon organization;
2. created one isolated, namespaced Neon PostgreSQL project;
3. generated a synthetic runtime secret;
4. deployed a namespaced Cloudflare Worker as build A;
5. injected `DATABASE_URL` and the synthetic secret through Wrangler secrets;
6. confirmed the configured Cron schedule through the Cloudflare API;
7. waited for `/health` to prove:
   - PostgreSQL connectivity;
   - runtime secret injection;
   - outbound provider-network connectivity;
8. wrote a durable synthetic event to Neon;
9. redeployed the same Worker as build B;
10. confirmed the Cron schedule remained attached;
11. confirmed `/health` for build B;
12. read the original event and proved it still carried build A as its writer;
13. waited for the real Cloudflare Cron Trigger to execute without a connected client;
14. observed the scheduled handler persist its marker to Neon;
15. deleted the Worker and temporary Neon project;
16. ran a tracked-secret leakage scan.

---

## Successful evidence

Workflow:

```text
run: 41189477201
job: 124503095078
result: success
```

Observed result:

```yaml
backend: cloudflare-workers+neon-postgres
status: PASS
source_sha: 80e16bb0563836d6bfd53ec066fab189446692e8

builds:
  initial: 80e16bb05638-a-1
  redeploy: 80e16bb05638-b-1

health:
  database: true
  secret_injected: true
  provider_egress: true

durable_state_survived_redeploy: true

event_driven_compute: true
client_connection_required_for_background_cron: false

cron:
  expression: "* * * * *"
  schedule_attached_before_and_after_redeploy: true
  persisted_state: true
  propagation_wait_seconds: 546.292

cleanup:
  worker_deleted: true
  neon_project_deleted: true

credential_values_recorded: false
database_uri_recorded: false
elapsed_seconds: 562.908
```

The successful run also completed the tracked-secret scan.

---

## Failure discovered and corrected

The first mutation attempt used a 210-second Cron wait window and failed only on the scheduled-event assertion.

That was a test-design error. Current Cloudflare documentation states that Cron Trigger configuration changes can take up to 15 minutes to propagate. The Worker deployment, Neon connectivity and durable redeploy path had already progressed far enough to show that the failure was isolated to the premature Cron deadline.

The probe was corrected to:

- use the canonical every-minute expression `* * * * *`;
- query the Cloudflare schedules API and fail immediately if the schedule is not attached;
- verify schedule attachment again after redeploy;
- allow 1020 seconds for propagation;
- record the observed propagation delay.

The corrected run passed with a real observed Cron delay of 546.292 seconds.

---

## Orphan-resource audit

The failed first mutation attempt left namespaced research resources behind despite best-effort `finally` cleanup. This was discovered because later read-only counts increased.

A dedicated fail-closed cleanup tool was added with this invariant:

> it may delete only resources whose visible name starts exactly with `amodex-p006b-`.

Cleanup evidence:

```text
workflow run: 41190356342
job: 124505718439
result: success
prefix: amodex-p006b-
```

Observed cleanup:

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

The cleanup credential scan also passed. No unrelated Worker or Neon project was modified.

This finding becomes a production requirement: cloud resource creation/deletion must be idempotent, namespaced, reconciled after ambiguous failures, and followed by explicit orphan detection. Best-effort `finally` cleanup alone is insufficient authority.

---

## Zero-cost boundary

This PoC used the current free accounts and did not intentionally enable a paid plan or paid fallback.

The zero-cost invariant remains stronger than this single successful run:

- current plan eligibility must be revalidated during implementation and deployment;
- no billing upgrade may be automatic;
- resource creation must fail closed when a required capability is paid-only or free capacity is exhausted;
- provider and hosting cost state remains explicit configuration/observation data, not a timeless assumption.

---

## Stage 1 consequences

Frozen direction from this PoC:

```text
Cloudflare Worker
  = thin stateless/event-driven Control Plane entrypoint

Neon Postgres
  = durable authority and initial coordination substrate

PostgreSQL
  = task/lease/fencing/event truth from P0-04

Cloudflare Cron
  = periodic reconciliation and maintenance wakeup
  != low-latency task queue

RunnerBackend
  = long-running execution
```

No Stage 1 component may treat a Worker instance, process memory, request lifetime, Cron delivery time, or Cloudflare-local transient state as authoritative.

P0-06B is therefore **closed as ADAPT**.
