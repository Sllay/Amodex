# Amodex Deployment and Operations Runbook

Status: **Normative deployment runbook — Part 3/3**

Primary initial host candidate: **Northflank Sandbox**

Operating policy: **zero-cost fail-closed**

This runbook defines the intended deployment topology, prerequisites, health/recovery procedures, and fallback behavior. Exact Northflank API fields/resource plan IDs must be revalidated during Stage 0/1 because hosting products change.

---

# 1. Current hosting baseline

As revalidated on 2026-10-31, Northflank advertises its Sandbox plan as:

- free;
- no card required to start;
- 2 services;
- 2 jobs;
- 1 database/addon;
- always on/no sleeping;
- up to the advertised monthly free resource allowance;
- compute explicitly described as limited;
- intended for hobby/testing rather than production SLA use.

This is a feasibility baseline, not a permanent contractual assumption.

Immediately before Stage 0 infrastructure PoCs and every major redeploy, verify:

- Sandbox still exists;
- free allowance/resource count;
- database availability;
- job API/lifecycle;
- persistent storage behavior;
- outbound networking;
- resource plan sizes;
- any new billing/card requirements.

If the host can begin charging automatically, Amodex deployment policy must disable/avoid that path unless owner explicitly opts in.

---

# 2. Initial Northflank topology

Target within current free resource shape:

```text
Northflank Project: amodex

SERVICE 1
Amodex Control Plane
- HTTP API
- scheduler/orchestrator loop
- Provider Governor
- recovery/reconciliation loop
- event stream

SERVICE 2
RESERVED initially
- do not deploy merely because slot exists
- candidate later: web UI or split worker gateway only after measurement

ADDON / DATABASE
PostgreSQL
- authoritative runtime state
- event log
- leases/fencing
- provider state
- checkpoint/evidence metadata

JOB 1
General Runner
- on demand
- coding/repository/tool workloads

JOB 2
Heavy/Visual Runner
- initially reserved for Godot or another high-resource profile
- may remain unused if Northflank free compute is insufficient
```

Do not keep Runner jobs permanently active.

---

# 3. Control Plane deployment contract

## 3.1 Container/process requirements

The Control Plane image must:

- use a pinned base image/digest for validated releases;
- run as non-root where practical;
- expose one application port;
- handle SIGTERM gracefully;
- stop accepting new leases before shutdown;
- persist no authoritative state only to local filesystem;
- perform database schema compatibility check on startup;
- expose health/readiness endpoints;
- emit structured logs without secrets;
- support immutable release/version identifier.

## 3.2 Startup sequence

Recommended sequence:

```text
process start
 -> load non-secret configuration
 -> resolve secret references/environment
 -> connect PostgreSQL
 -> verify schema version
 -> verify zero-cost policy
 -> initialize provider registry (without unnecessary live calls)
 -> start reconciliation scan
 -> start scheduler/lease recovery
 -> mark readiness true
 -> accept API traffic
```

A service that cannot verify database schema/policy must remain unready rather than serving partially.

## 3.3 Shutdown sequence

On SIGTERM/redeploy:

```text
readiness false
 -> stop accepting new run/task leases
 -> stop scheduling new Runner/provider work
 -> allow bounded in-flight DB transaction completion
 -> persist shutdown event/heartbeat state where useful
 -> cancel/hand off background loops
 -> exit
```

Do not wait indefinitely for external provider/Runner operations. Durable reconciliation handles uncertain outcomes after restart.

---

# 4. PostgreSQL deployment

PostgreSQL is the initial authoritative durable store.

## 4.1 Required characteristics

- persistent across Control Plane redeploys;
- private/internal network access where platform supports it;
- TLS according to host capability;
- credentials delivered through secret mechanism;
- no DB password committed/logged;
- backup/export procedure documented before real project use;
- connection pool sized for free-plan limits.

## 4.2 First deployment

Stage 1 initial deployment procedure:

1. provision DB addon within free plan;
2. capture connection as secret/reference;
3. deploy Control Plane with migration command disabled from normal request path;
4. run migration job/one-off command intentionally;
5. verify schema version;
6. create health test record/event;
7. redeploy Control Plane;
8. verify record persists;
9. run lease claim/recovery smoke.

## 4.3 Migration rule

Production startup must not silently apply arbitrary destructive migrations.

For simple additive early migrations an explicit startup migration mode may be accepted by ADR, but default mature behavior is:

```text
build release
 -> backup/verify
 -> run migration command/job
 -> verify schema
 -> deploy application release
```

---

# 5. Secret configuration

Minimum secret families:

- PostgreSQL connection;
- provider API keys/credentials;
- GitHub App private key/installation configuration when introduced;
- Northflank API credential if Control Plane launches jobs through API;
- webhook secret where applicable.

Rules:

- secrets live in host secret groups/environment injection, not repo;
- worker receives only task-required secret material;
- logs redact recognized secret fields;
- provider keys are not copied into Git worktrees;
- secrets are referenced by opaque ID in database records;
- rotation must not require source code change.

Before public repository release, run canary-secret scan across Git history and current tree.

---

# 6. Northflank General Runner lifecycle

The exact API implementation is validated by P0-07.

Conceptual lifecycle:

```text
Control Plane creates Runner record REQUESTED
        |
        v
Northflank job launch requested
        |
        v
STARTING
        |
        v
Runner process registers/proves runner_id + lease token
        |
        v
capability/bootstrap probes
        |
        v
RUNNING
        |
        v
execute task attempt
        |
        +--> push required checkpoint
        +--> upload/report evidence
        |
        v
COMPLETED / FAILED / CANCELLED
        |
        v
job terminates
```

The Runner filesystem is disposable.

A Runner must not be accepted solely because Northflank reports the container as running. Amodex requires its own authenticated registration/capability probe.

---

# 7. Heavy/Godot Runner topology

Preferred containment:

```text
Heavy Runner container/job
|
├── Amodex Worker/Hermes adapter
├── Git worktree
├── Serena
├── Godot MCP (local stdio/process)
├── Godot 4.7.2/project-pinned binary
├── Xvfb/equivalent when visual
├── Mesa/llvmpipe when required
└── artifact capture
```

Godot MCP/editor bridge remains local to Runner network/process space.

Never publish the Godot editor bridge as a public endpoint.

## 7.1 Headless first

Try cheap validation first:

- bootstrap;
- import/parser;
- LSP/Serena;
- headless tests;
- project diagnostics.

Only request visual profile when acceptance criteria need:

- input interaction;
- UI layout;
- camera/render behavior;
- screenshots;
- live scene/runtime inspection unavailable headlessly.

---

# 8. RunnerBackend fallback policy

Northflank is a backend, not a dependency of task semantics.

Fallback selection is capability/resource based:

```text
Task requires general runner
 -> Northflank free capacity available? yes -> use
 -> no -> another eligible zero-cost backend?
       -> yes -> use
       -> no -> WAITING_RUNNER

Task requires godot.visual
 -> backend with proven godot.visual capability?
       -> yes -> use
       -> no -> WAITING_RUNNER / capability unavailable
```

Never switch to paid compute automatically.

---

# 9. GitHub Actions fallback

GitHub Actions is a candidate fallback for bounded validation/Runner workloads, not a guaranteed unlimited-free backend.

Before enabling for a repository:

- inspect current Actions billing/minute policy for repository visibility/account;
- confirm zero-cost allowance;
- define maximum job duration;
- ensure no self-hosted/billed resource hidden dependency;
- configure concurrency to prevent runaway usage.

Good candidate workloads:

- CI validation;
- disposable repository integration tests;
- Godot headless/visual validation if environment proves viable;
- artifact capture.

Poor candidate workloads:

- indefinite interactive worker sessions;
- long background orchestration state;
- always-on Control Plane.

Amodex should represent Actions as an asynchronous RunnerBackend with callback/polling, not pretend it is a persistent shell.

---

# 10. Local container backend

Required for development/testing even when production host is Northflank.

Purpose:

- deterministic integration tests;
- fault injection;
- developer reproduction;
- no cloud quota consumption for every test;
- RunnerBackend contract conformance.

The local backend is not permission to rely on the user's local PC for production durability.

Termux remains a client; if local Android execution exists later, it is a separate optional backend.

---

# 11. Health endpoints

Mature Control Plane should expose at least:

## Liveness

Process event loop/application is alive.

Must not perform expensive provider/GitHub checks.

## Readiness

Requires:

- DB reachable;
- schema compatible;
- critical configuration valid;
- scheduler/reconciliation initialized.

Provider unavailability should usually degrade provider capability rather than make the entire API unready.

## Diagnostic status

Authenticated owner/admin endpoint can summarize:

- DB status;
- scheduler status;
- active leases;
- provider circuit states;
- Runner backend states;
- GitHub integration state;
- zero-cost policy state;
- current release/version.

No secret values returned.

---

# 12. Deploy procedure

For each release after Stage 1:

1. identify candidate Git SHA;
2. local/CI Tier A/B tests pass;
3. build immutable container image;
4. record image digest;
5. scan dependencies/secrets as configured;
6. verify migration compatibility;
7. backup DB if migration risk requires it;
8. run migration intentionally;
9. deploy new Control Plane image;
10. wait for readiness;
11. execute remote smoke run using provider simulator or bounded free provider probe;
12. verify scheduler/reconciliation;
13. verify zero-cost mode still enabled;
14. record deployment evidence/version.

Do not deploy `latest` without immutable digest/version provenance for a validated release.

---

# 13. Rollback procedure

Application rollback:

1. mark deployment unhealthy/stop promotion of new work if necessary;
2. pause new task leases;
3. inspect in-flight side effects;
4. redeploy previous known-good image digest;
5. verify schema compatibility;
6. reconcile outstanding runs/leases;
7. resume scheduling;
8. create incident record/root cause.

Database rollback is not assumed possible merely because application image can roll back.

Migration design must document forward-fix vs DB restore requirements.

---

# 14. Control Plane crash/restart runbook

After unexpected restart:

```text
startup
 -> DB/schema check
 -> acquire singleton/reconciliation lease if architecture requires
 -> scan ACTIVE/LEASED/EXECUTING states
 -> mark expired leases for recovery evaluation
 -> reconcile UNKNOWN_OUTCOME tool/provider/Git operations
 -> observe remote Runner states
 -> observe target Git refs for pending promotions
 -> restore provider cooldown/circuit state from durable records where required
 -> resume READY work
```

Do not immediately re-run all EXECUTING tasks.

---

# 15. Runner loss runbook

When heartbeat expires:

1. stop trusting old fencing token;
2. query backend status;
3. classify attempt as still running, terminated, unreachable, or unknown;
4. inspect side-effect journal;
5. inspect last pushed checkpoint;
6. reconcile external operations;
7. if safe, create new attempt/Runner from checkpoint;
8. otherwise mark `RECOVERY_REQUIRED` or `UNKNOWN_OUTCOME/BLOCKED`;
9. never assume local uncommitted workspace survives.

---

# 16. Provider outage/quota runbook

When provider route hits quota/outage:

1. normalize error;
2. update quota/cooldown/circuit state;
3. release/complete ProviderLease appropriately;
4. evaluate equivalent free fallback;
5. if none, task -> `WAITING_PROVIDER`;
6. surface owner-visible reason/next eligible retry;
7. never enable paid model automatically.

If provider authentication is invalid, do not hammer retries; mark credential route unhealthy and request configuration action.

---

# 17. GitHub outage runbook

If GitHub unavailable:

- stop new remote checkpoint/promotion operations;
- local Runner may continue read/implementation only until checkpoint policy requires remote durability;
- do not destroy Runner containing meaningful unpushed work unless explicitly accepted;
- promotion remains blocked;
- periodically probe with bounded backoff;
- after recovery, observe remote refs before retrying any ambiguous operation.

GitHub outage must not cause fake `PROMOTED` state.

---

# 18. Backup and restore

Before Stage 6 completion, prove:

## Backup

- PostgreSQL logical/platform-supported backup/export;
- encryption/access appropriate to host;
- records backup timestamp/schema version;
- no secrets accidentally exported into public artifact storage.

## Restore

Restore into a separate test DB/environment and verify:

- projects/runs/tasks/events reconstruct;
- lease state can be safely normalized after restore;
- checkpoint/candidate/promotion metadata intact;
- provider secret references fail clearly if secrets are not copied;
- GitHub reconciliation can re-observe remote state.

A backup never tested by restore is not a completed backup system.

---

# 19. Observability operations

Minimum owner-visible operational signals:

- release version;
- active runs/tasks;
- blocked/waiting reason;
- provider availability/circuit state;
- Runner availability;
- last successful DB interaction;
- queue depth;
- active leases;
- recent recovery events;
- zero-cost policy status.

Avoid exposing chain-of-thought, raw entire prompts, repository secrets, or provider keys.

---

# 20. Free-tier exhaustion behavior

The platform must have explicit behavior for each resource type.

## Provider free quota exhausted

`WAITING_PROVIDER`.

## Runner free capacity exhausted

`WAITING_RUNNER`.

## Northflank resource limit prevents new job/service

Do not upgrade plan. Try configured zero-cost fallback or wait.

## GitHub Actions free allowance unavailable

Backend becomes ineligible; do not run billed job.

## Storage/database near free limit

- stop retaining nonessential large artifacts first according to retention policy;
- alert owner;
- never delete authoritative task/promotion state blindly;
- pause new large work if necessary.

---

# 21. Infrastructure change policy

Changes that require ADR/revalidation include:

- moving Control Plane host;
- replacing PostgreSQL authority;
- adding Redis as required dependency;
- changing RunnerBackend contract semantics;
- exposing MCP over network;
- enabling paid compute;
- changing container isolation/security model;
- moving secrets into a new store;
- adding an always-on second service as architectural requirement.

Scaling within the same accepted backend/profile may not require ADR if contracts remain unchanged.

---

# 22. Northflank acceptance decision

Northflank becomes the accepted primary host only after P0-06.

Northflank becomes an accepted General Runner backend only after P0-07.

Northflank becomes an accepted Godot headless backend only after P0-08.

Northflank becomes an accepted Godot visual backend only after P0-09.

These decisions are independent.

Possible final topology is therefore legitimately:

```text
Northflank:
  Control Plane + PostgreSQL + General Runner

GitHub Actions/other free backend:
  Godot visual Runner
```

without considering that an architectural failure.

---

# 23. Deployment Definition of Done

A deployment is not complete until:

- exact source SHA and image digest recorded;
- migration state verified;
- readiness green;
- durable smoke run completes;
- restart/reconciliation smoke passes for major releases;
- zero-cost mode confirmed;
- no new paid resources enabled;
- secrets scan/log check clean;
- rollback target known;
- known limits recorded.

The hosting dashboard showing “running” is not sufficient evidence.