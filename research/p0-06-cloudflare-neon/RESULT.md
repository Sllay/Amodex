# P0-06B — Cloudflare Workers + Neon Postgres Control Plane

Status: **BLOCKED_EXTERNAL_CREDENTIALS**

This is the replacement Stage 0 hosting experiment after Northflank was rejected as the primary Control Plane host for the owner's current account because its single free project slot is already occupied by an unrelated project that must not be touched.

The experiment preserves the same architectural rule: durable truth remains in PostgreSQL; the HTTP/control surface is disposable and may be serverless.

## Why this backend

Revalidated on 2026-10-31:

### Cloudflare Workers Free

- Python Workers are first-class and support FastAPI/Pydantic-compatible packages.
- Free plan includes 100,000 requests/day.
- Free plan supports Cron Triggers.
- Cloudflare Queues are available on Workers Free.
- Secrets are injected as encrypted Worker secrets.
- Workers do not require an always-on VM to remain reachable.

### Neon Free

- no credit card required for Free signup;
- up to 100 projects on the current Free plan;
- 1 GB Postgres storage per project;
- 100 CU-hours per project/month;
- autoscaling/scale-to-zero;
- Postgres remains normal Postgres, preserving the P0-04 lease/fencing design;
- Functions/Data API exist, but P0-06B does not make them authoritative unless explicitly adopted by evidence.

## Architectural shape under test

```text
Termux / dashboard / webhook / cron
               |
               v
       Cloudflare Worker
       Control Plane edge API
               |
               v
          Neon Postgres
       durable authority/state
               |
               +--> Provider API
               +--> RunnerBackend
```

The Worker is not a Runner. Heavy coding, Godot, shell, Git worktrees and Hermes remain outside Workers.

## Required GitHub Actions secrets

```text
CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID
NEON_API_KEY
```

No secret value may be committed, printed, stored in RESULT files, or returned from the probe.

## Credential gate

The first P0-06B workflow performs only read-only account checks:

1. authenticate to Cloudflare using the configured account ID + API token;
2. verify Workers API access;
3. authenticate to Neon;
4. list accessible Neon projects;
5. record only non-sensitive capability metadata/counts;
6. scan tracked files for accidental secret leakage.

No infrastructure mutation occurs until all three credentials pass this gate.

## Full P0-06B pass criteria

After the credential gate passes, the mutation phase must prove all of the following before P0-06B can close:

```text
create isolated Neon project
create minimal durable schema/event row
create/deploy namespaced Cloudflare Worker
inject Neon connection material as Worker secret
GET /health works
provider egress works
write/read durable synthetic event
redeploy Worker to a new version
same event survives redeploy
Cron Trigger wakes scheduler path
Queue enqueue/dequeue path works within free tier
no durable truth depends on Worker local memory
no paid product/plan is enabled
no secret appears in logs/artifacts/repository
cleanup or explicit retention is recorded
```

## Zero-cost enforcement

The experiment must fail closed if either platform requires a paid plan, billing upgrade, or resource outside current free allowances.

No automatic upgrade or paid fallback is permitted.

## CPU/runtime adaptation

Cloudflare Workers Free has a small per-request CPU budget. Therefore the expected Control Plane design is intentionally thin:

- validate request/identity;
- transact durable state;
- enqueue/claim work;
- call lightweight provider/governor endpoints;
- wake/schedule orchestration;
- return status/events.

Long-running reasoning and execution remain Runner responsibilities.

This is an **ADAPT candidate**, not a silent change to the Stage 0 architecture.

## Current decision

**BLOCKED_EXTERNAL_CREDENTIALS.**

The owner must create free Cloudflare and Neon accounts and add the three GitHub Actions secrets above. The credential probe is read-only and will not mutate either account.
