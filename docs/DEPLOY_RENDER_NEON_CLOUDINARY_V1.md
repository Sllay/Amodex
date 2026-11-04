# Amodex — Render + Neon + Cloudinary Deployment V1

Status: production deployment contract for the zero-cost V1.

## Absolute database boundary

The existing older Neon project is **out of scope**. Do not use, migrate, inspect, reset, branch, or write to it for Amodex.

Amodex production must use only the newly created Neon project named **`Amodex`**.

The production migration runner enforces three independent values before it writes schema:

1. `AMODEX_DATABASE_URL` — the **pooled** Neon URL used by the running FastAPI service.
2. `AMODEX_MIGRATION_DATABASE_URL` — the **direct** Neon URL used only by the migration runner.
3. `AMODEX_DATABASE_ENDPOINT_GUARD` — the endpoint ID of the new `Amodex` Neon endpoint, for example `ep-example-name-123456`.

The runner normalizes the pooled host by removing `-pooler` and requires all three values to identify the same endpoint. If they differ, startup fails before any migration SQL is executed.

For Neon URLs, TLS is mandatory. The accepted `sslmode` values are `require`, `verify-ca`, or `verify-full`.

## Render secrets

`render.yaml` intentionally contains no credentials. During the first Blueprint creation, provide these values in the Render dashboard:

- `AMODEX_DATABASE_URL`
- `AMODEX_MIGRATION_DATABASE_URL`
- `AMODEX_DATABASE_ENDPOINT_GUARD`
- `CLOUDINARY_URL`

Do not paste these values into source files, issues, pull requests, chat logs, screenshots, or CI output.

### Getting the two Neon URLs safely

Open **only** the Neon project named `Amodex`.

For `AMODEX_DATABASE_URL`:

1. Open Connection Details.
2. Select the production branch/database intended for Amodex.
3. Enable **Pooled connection**.
4. Copy the PostgreSQL connection string.
5. Confirm the hostname contains `-pooler`.

For `AMODEX_MIGRATION_DATABASE_URL`:

1. Stay in the same `Amodex` project and same branch/database.
2. Disable **Pooled connection**.
3. Copy the direct PostgreSQL connection string.
4. Confirm the hostname does **not** contain `-pooler`.

For `AMODEX_DATABASE_ENDPOINT_GUARD`, use the endpoint ID shared by those two hosts. Given:

```text
ep-example-name-123456-pooler.us-east-2.aws.neon.tech
ep-example-name-123456.us-east-2.aws.neon.tech
```

set:

```text
AMODEX_DATABASE_ENDPOINT_GUARD=ep-example-name-123456
```

If either connection string is accidentally copied from the old Neon project, the endpoint identities will not match the guard and the deploy will stop.

## Cloudinary

Set:

```text
AMODEX_MEDIA_STORE_BACKEND=cloudinary
CLOUDINARY_URL=<Render secret>
AMODEX_CLOUDINARY_PREFIX=amodex-production
```

`CLOUDINARY_URL` uses Cloudinary's standard server-side credential format and must remain secret.

Amodex does not store Cloudinary URLs in PostgreSQL as authority. The application continues to store opaque references such as:

```text
media://sha256/<digest>
```

The Cloudinary backend maps that digest to a deterministic raw object and uploads it with delivery type `authenticated`. Downloads are time-limited signed API requests. Every downloaded object is hashed again and rejected if its SHA-256 does not match the `media://` reference.

## Production migration behavior

Render starts Amodex with:

```bash
python scripts/migrate_production.py && \
uvicorn amodex.api.workspace_app:app --host 0.0.0.0 --port $PORT
```

The web server does not start if migrations fail.

The migration runner:

- validates the runtime URL, direct migration URL, endpoint guard, and Neon TLS before connecting;
- takes a PostgreSQL advisory lock so overlapping deploys cannot migrate concurrently;
- applies migration groups in deterministic order: root, Stage 4, Stage 5, Stage 6, post-baseline;
- records each migration and its SHA-256 checksum;
- rejects an already-applied migration if its SQL file was later modified;
- is safe to execute again after restart/deploy;
- never prints database credentials.

## Render configuration

The Blueprint uses:

```text
runtime: python
plan: free
PYTHON_VERSION=3.13.5
healthCheckPath=/
```

The build command installs the project itself:

```bash
pip install .
```

No persistent Render filesystem is required. Durable database state lives in Neon and durable attachment bytes live in Cloudinary.

## Zero-cost fail-closed policy

The deployment contract is:

```text
No payment method is part of the Amodex deployment configuration.
No automatic paid fallback is permitted.
A quota/service failure must degrade, block, or stop the affected capability instead of enabling paid capacity.
Secrets are environment variables only.
```

Provider/model keys are separate from the Neon/Cloudinary deployment secrets. Add model-provider credentials later only as Render secrets and only when the associated Amodex billing class is explicitly configured as `free`.

## Deployment sequence

1. Merge the deployment PR only after CI is green.
2. Create/sync the Render Blueprint from `render.yaml`.
3. Enter the four required secrets using **only** the new Neon `Amodex` project and the intended Cloudinary product environment.
4. Let the Render start command run the guarded migrations.
5. Confirm the service health check succeeds.
6. Test a normal chat request.
7. Test one attachment upload and retrieval.
8. Confirm PostgreSQL stores a `media://sha256/...` reference rather than binary payload or Cloudinary credentials.

Do not perform any migration against the older Neon project as a deployment test.
