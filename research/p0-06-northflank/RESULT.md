# P0-06 — Northflank Control Plane Hosting / Persistence

Status: **BLOCKED_EXTERNAL_CREDENTIAL**

P0-06 is prepared to test the Amodex Control Plane on the real Northflank Sandbox. It is not considered complete until a real Northflank account/API token executes the deployment, restart, rebuild/redeploy and persistence checks.

## Current Northflank baseline — 2026-10-31

Current official Northflank documentation/pricing was revalidated before writing this PoC.

Sandbox currently provides:

```text
price: Free
card required: no
services: 2
jobs: 2
addons/databases: 1
Northflank Cloud resource allowance: up to $20/month
sleep behavior: always on / no sleeping
```

The PoC deliberately targets the smallest standard deployment plan currently suitable for the service/database:

```text
nf-compute-20
0.2 vCPU
512 MiB RAM
```

Northflank currently reports `nf-compute-20` at roughly $4.40/month if run continuously. The Docker build uses a build-capable plan only for the short build duration; builds are billed by runtime rather than reserving that monthly amount continuously.

The probe verifies plan IDs again from `/v1/plans` before allocating resources.

## External credential boundary

Northflank's API uses Bearer authentication. A real account must generate a personal/team API token.

Required GitHub Actions secret:

```text
NORTHFLANK_API_TOKEN
```

Optional GitHub Actions secret/variable:

```text
NORTHFLANK_PROJECT_ID
```

If no project ID is supplied, the probe searches for `Amodex Stage0` and creates it if absent.

The token must never be committed, pasted into chat, written to RESULT files, or emitted in logs.

## Fixture

Research-only files:

```text
research/p0-06-northflank/app.py
research/p0-06-northflank/Dockerfile
research/p0-06-northflank/northflank_probe.py
```

The service is intentionally tiny. It exposes only:

```text
GET  /health
POST /event
GET  /event?marker=...
```

Durable truth lives in PostgreSQL. The process receives a new random `instance_id` every start so restart/redeploy can prove the process changed while database state remained.

## Database

The API probe creates/reuses one internal PostgreSQL addon:

```text
name: amodex-p006-db
type: postgresql
version: 16-latest
plan: nf-compute-20
replicas: 1
external access: false
```

The addon stays private to the Northflank project.

The probe retrieves the normal, non-admin `POSTGRES_URI` connection string through the official addon-credentials endpoint and injects it as:

```text
DATABASE_URL
```

No connection string is printed or written to result artifacts.

## Secret injection

The probe creates/updates a runtime-only secret group containing:

```text
DATABASE_URL=<redacted>
AMODEX_P0_06_SECRET=<random synthetic sentinel>
```

The service reports only whether the secret exists and a short SHA-256 fingerprint; it never returns the secret itself.

Pass criterion:

```text
secret_injected: true
```

## Service deployment

The Control Plane fixture is deployed as a Northflank combined service built from an exact GitHub commit archive:

```text
https://github.com/Sllay/Amodex/archive/<exact-source-sha>.tar.gz
```

This avoids requiring a linked private Git provider for the Stage 0 fixture and preserves exact-source identity.

Build file:

```text
/research/p0-06-northflank/Dockerfile
```

Runtime plan:

```text
nf-compute-20
1 instance
public HTTP :8080
```

Readiness:

```text
GET /health
```

## Health contract

`/health` only returns `ok: true` when all three are true:

```text
PostgreSQL query succeeds
Northflank runtime secret exists
outbound DNS/TLS/HTTP connectivity to Google's Gemini API host succeeds
```

The provider egress probe is synthetic and unauthenticated; any normal HTTP 4xx from the Google endpoint still demonstrates DNS/TLS/outbound network reachability. No user/project content is sent.

## Durable state test

1. Start service and record `instance_id=A`.
2. Write a synthetic marker through `POST /event`.
3. Read marker back from PostgreSQL.
4. Restart service through Northflank API.
5. Require healthy `instance_id=B`, where `B != A`.
6. Read the same marker and require it unchanged.
7. Trigger a new build/redeploy of the same exact source SHA.
8. Require healthy `instance_id=C`, where `C != B`.
9. Read the same marker and require it unchanged again.

Expected:

```text
durable_state_survived_restart: true
durable_state_survived_redeploy: true
```

This proves that process/container identity is disposable while PostgreSQL remains authoritative.

## Resource measurements

The probe requests real Northflank service metrics for:

```text
cpu
memory
```

It records only metric units/sample values and maxima, not credentials.

These measurements are the host-real counterpart to the GitHub Actions PostgreSQL proxy measurement from P0-04.

## API automation proof

The experiment uses only documented Northflank API operations for infrastructure control:

```text
list plans
list/create project
create/read PostgreSQL addon
read addon credentials
create/update secret group
create combined service
read public port/DNS
restart service
start rebuild
read service metrics
delete PoC service/secret/addon
```

This is important because Stage 1 cannot depend on clicking the Northflank dashboard manually for normal lifecycle operations.

## Cleanup

By default the probe deletes PoC-only:

```text
service
secret group
PostgreSQL addon
```

The `Amodex Stage0` project is intentionally retained because P0-07 and P0-08 belong to the same host feasibility lineage and a Sandbox account can have project limits.

Resources can be retained for debugging only with an explicit `--keep-resources` run.

## Pass criteria

P0-06 can only close after a real remote run demonstrates:

```text
Sandbox/API authentication succeeds
required compute plans available
PostgreSQL addon reaches running
Control Plane reaches healthy public endpoint
DB connectivity true
secret injection true
provider egress true
durable marker written/read
restart creates a new process instance
durable marker survives restart
rebuild/redeploy creates a new process instance
durable marker survives redeploy
real Northflank CPU/RAM metrics collected
API lifecycle works without dashboard clicks
no token/database URI leakage
PoC cleanup succeeds or retained resources are explicitly justified
```

## Current decision

**BLOCKED_EXTERNAL_CREDENTIAL.**

The application fixture and API orchestration probe are implemented. The missing evidence is a real Northflank Sandbox account/API token.

A Northflank plugin is not currently available in the connected ChatGPT plugin catalog, so direct platform execution from this chat is unavailable. The GitHub Actions workflow is therefore the secure execution boundary for the real API token.

Once `NORTHFLANK_API_TOKEN` exists as a repository secret, the same Stage 0 branch can execute the real P0-06 without putting the token in source or chat.
