# P0_ACTIVATION_V1

Status: **ACTIVATION_CANDIDATE_VALIDATED**

> Historical checkpoint: the `/readyz` Render health-check setting described below
> records the activation candidate at that time. The current `render.yaml` uses
> `/healthz` for process liveness, as enforced by
> `test_render_liveness_probe_remains_separate_from_database_readiness`.
> `/readyz` remains the independent database/schema/zero-cost readiness gate;
> a successful liveness response does not establish functional readiness.
> See [the current deployment contract](../DEPLOY_RENDER_NEON_CLOUDINARY_V1.md#health-and-readiness).
> This note preserves the earlier evidence and does not change any Render setting.

## Scope

This checkpoint validates the P0 deployment/activation candidate without deploying it to production.

Validated activation behavior:

1. Liveness is separated from readiness.
2. Readiness verifies PostgreSQL reachability, exact migration compatibility/checksums, and the zero-cost fail-closed policy.
3. Render deployment health is configured to use readiness instead of process-only liveness.
4. Release diagnostics expose the deployed revision without exposing secrets.
5. Web/Termux can run a read-only remote smoke through `AMODEX_URL`.
6. Durable project/chat/message state survives a real Control Plane process restart.
7. Production migrations remain idempotent on the second boot.
8. Mobile web behavior remains green after restart.
9. Model Intelligence remains fail-closed until an eligible measured free route exists.

No production deployment is part of this checkpoint.

## Validated revision

- Branch: `p0/activation-v1`
- Validated code SHA: `583f698efd1d516cf166d8b3f1457038609d7239`
- GitHub Actions run: `41423882180`
- Job: `125205681916` — **SUCCESS**

The checkpoint documentation commit is expected to be newer than the validated code SHA. The SHA above is the exact code revision exercised by the final activation matrix.

## Health model

### `/healthz`

Pure liveness only.

It intentionally avoids PostgreSQL, providers, GitHub, and other external dependency checks. A provider outage therefore does not incorrectly make the whole process look dead.

### `/readyz`

Public deployment readiness surface.

It validates:

- PostgreSQL is reachable;
- the migration manifest can be discovered;
- every expected production migration is applied;
- stored migration checksums match the repository migrations;
- the zero-cost configuration is valid;
- explicitly configured provider pools do not violate the free-only policy;
- enabled STT must be declared with free billing.

Response is `200` only when the deployment is ready; otherwise it is `503`.

To avoid turning frequent Render health checks into unnecessary Neon traffic, readiness is cached inside the process for a short bounded period:

- default: 15 seconds;
- configurable with `AMODEX_READINESS_CACHE_SECONDS`;
- clamped to 1–60 seconds.

The first readiness request remains a real dependency/schema probe.

## Render activation boundary

`render.yaml` now uses:

```yaml
healthCheckPath: /readyz
```

The existing startup command remains migration-first:

```text
python scripts/migrate_production.py && uvicorn amodex.api.workspace_app:app --host 0.0.0.0 --port $PORT
```

This means the candidate requires both successful migrations and application readiness before it is considered healthy.

## Rich activation diagnostics

The authenticated diagnostic route is:

```text
GET /v1/activation/status
```

It exposes safe deployment diagnostics such as:

- release version;
- deployed Git SHA;
- host class;
- database reachability;
- schema compatibility;
- migration counts/mismatch counts;
- zero-cost state;
- Model Intelligence activation state.

It does not expose connection strings, passcodes, provider keys, Cloudinary secrets, or other credentials.

The pre-existing Control Plane route:

```text
GET /v1/system/status
```

is intentionally preserved. The activation route uses its own path to avoid FastAPI route-order collisions and backward-compatibility regressions.

## Release identity

Activation diagnostics identify the running revision using:

1. `RENDER_GIT_COMMIT` when supplied by Render;
2. `AMODEX_RELEASE_SHA` as the explicit fallback used by isolated validation;
3. `unknown` only when neither source exists.

The remote smoke can require an expected SHA/prefix and fails if the running revision does not match.

## Remote Web / Termux smoke

`scripts/smoke_remote.py` is a standard-library read-only activation client.

It supports:

- `--url` or `AMODEX_URL`;
- optional `AMODEX_ACCESS_PASSCODE` for private deployments;
- optional expected Git SHA;
- optional `--require-benchmarks`.

The smoke validates:

1. `/healthz`;
2. `/readyz`;
3. zero-cost readiness;
4. deployed SHA when requested;
5. authenticated `/v1/activation/status`;
6. Model Intelligence benchmark state.

It never prints the passcode or provider credentials.

## Restart persistence proof

The final CI performed a real two-process restart sequence against the same isolated PostgreSQL service.

### First process

1. production migrations were applied;
2. PostgreSQL readiness passed;
3. the first Control Plane process started;
4. remote smoke passed;
5. the probe created a unique project;
6. it created a project chat;
7. it wrote exactly one OWNER message and recorded its IDs.

### Restart boundary

1. the first Control Plane received `SIGTERM`;
2. the workflow required the process to terminate;
3. `scripts/migrate_production.py` ran again against the same PostgreSQL database;
4. the second migration pass was required to report `MIGRATIONS_OK applied=0`.

### Second process

1. a new Control Plane process started against the same PostgreSQL service;
2. `/readyz` passed again;
3. the persistence probe fetched the original chat;
4. the same workspace ID survived;
5. the same chat ID survived;
6. the same OWNER message ID/content survived;
7. exactly one matching OWNER message existed;
8. remote smoke passed again.

This proves the P0 application state used by the probe is PostgreSQL-durable rather than process-memory state.

## Mobile regression after restart

After the second boot, the workflow ran the full Chromium mobile web E2E baseline against the restarted Control Plane.

This preserved the previous P0 guarantees, including the Workspace UI, local-first cache behavior, upload path, and browser interaction authority.

## Zero-cost / benchmark boundary

The activation candidate remains fail-closed for automatic model inference.

The final workflow explicitly checks the benchmark gate:

- if an eligible measured free route is already `READY`, the smoke may pass with `--require-benchmarks`;
- otherwise the candidate must remain in an explicit gated/degraded state rather than silently selecting an unmeasured or paid route.

The isolated CI does not carry real provider credentials, so it cannot perform the final live provider benchmark itself. That benchmark remains a production/staging activation action using an explicitly free credential pool.

No paid fallback was introduced.

## Regression preservation

The final activation run also preserved the previous P0 gates:

- frontend single-authority regression;
- bounded PostgreSQL runtime pool;
- streaming attachment pipeline;
- Cloudinary media boundaries;
- private ETag/304 cache behavior;
- IndexedDB/local-first state;
- Service Worker boundaries;
- workspace snapshot integration.

## Final CI evidence

Run: `41423882180`

Job: `125205681916`

All meaningful steps completed successfully:

- isolated runtime setup;
- compile;
- P0 regression suite;
- production migrations;
- migrated PostgreSQL readiness;
- first Control Plane startup;
- remote smoke before restart;
- durable state seed;
- graceful SIGTERM;
- migration idempotency (`applied=0`);
- second Control Plane startup;
- durable state verification;
- remote smoke after restart;
- Chromium mobile web regression after restart;
- explicit benchmark gate;
- server/container cleanup.

Final job conclusion: **SUCCESS**.

## Explicit limits / non-goals

- This checkpoint does **not** mean production is already activated.
- No Render production deployment was performed.
- No production Neon database was modified by this validation.
- The user's previous/old Neon project was not touched.
- No real provider secret was added to CI.
- No real free-provider benchmark was executed in isolated CI because provider credentials are intentionally absent.
- Provider availability is treated as capability degradation; database/schema/zero-cost configuration determine core deployment readiness.
- Live Render + Neon + Cloudinary validation still requires deploying this candidate to the controlled production environment and running the remote smoke against that deployed SHA.

## Result

`P0_ACTIVATION_V1 = ACTIVATION_CANDIDATE_VALIDATED`
