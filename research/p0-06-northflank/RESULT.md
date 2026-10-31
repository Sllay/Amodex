# P0-06A — Northflank Control Plane Hosting / Persistence

Status: **REJECT_AS_PRIMARY_FOR_CURRENT_ACCOUNT**

Northflank remains technically suitable enough to keep as a future RunnerBackend/hosting candidate, but it is rejected as the primary Stage 0 Control Plane host for the owner's current account because the free Sandbox project slot is already occupied by an unrelated existing project that must not be touched.

This is an account-capacity rejection, not a claim that Northflank itself failed technically.

## Evidence — 2026-10-31

The real Northflank credential was injected into GitHub Actions and accepted by the API boundary.

Workflow evidence:

```text
workflow run: 41185150144
rerun job: 124491987114
credential detection: PASS
real Northflank probe: EXECUTED
```

The first infrastructure mutation was project creation:

```text
PUT /v1/projects
```

Northflank returned:

```text
HTTP 409 Conflict
Maximum number of free projects reached
```

No service, secret group, PostgreSQL addon, job, or Amodex project was created by that failed run.

## Safety decision

The existing Northflank project is explicitly out of scope.

The Stage 0 workflow must not:

- reuse it implicitly;
- create namespaced resources inside it;
- inspect or alter its existing services beyond what is necessary to observe account-level project capacity;
- delete or rename it;
- require the owner to risk an unrelated deployment just to make Amodex pass a PoC.

A temporary proposal to reuse the single existing project automatically was immediately rejected by the owner and reverted before being accepted as architecture.

## Zero-cost consequence

Creating another Northflank account is not available to the owner at this time. Therefore the Northflank-specific P0-06 path cannot satisfy the current zero-cost primary-host requirement without violating the isolation requirement above.

Per the Stage 0 contract, this does **not** block Amodex overall because P0-06 is only blocking for Northflank as the primary host. The system architecture already requires host portability.

## Decision

```text
Northflank primary Control Plane: REJECT for current account
Northflank technology in general: NOT REJECTED
Existing Northflank project: PROTECTED / DO NOT TOUCH
Northflank probe code: retained as failure reproduction and future backend reference
```

The next experiment is **P0-06B**, using a separate zero-cost backend while preserving the same Control Plane contract.

## Replacement direction

Current 2026-10-31 research favors an event-driven serverless Control Plane rather than another always-on hobby VM:

```text
Cloudflare Workers Free
        +
Neon Free Postgres
```

Reasons:

- Cloudflare Workers supports Python, secrets, Cron Triggers, Queues and the Free plan;
- the Control Plane can wake on requests/events instead of paying for idle VM time;
- Neon provides real Postgres with scale-to-zero and a large free project allowance;
- PostgreSQL remains authoritative, preserving the P0-04 coordination model;
- Runner execution remains a separate backend concern, so Workers CPU-time limits do not become worker limits.

P0-06B must still prove the same important properties: health, durable state, secret isolation, provider egress, deploy/redeploy recovery, scheduler/event wake-up and no paid fallback.
