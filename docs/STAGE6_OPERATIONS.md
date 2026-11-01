# Stage 6 Operations Runbook

Status: **operator procedure exercised by Stage 6 CI**

This runbook covers daily-use deployment, health, backup/restore, upgrade and
recovery. It assumes the existing zero-cost deployment policy and does not grant
workers direct infrastructure/database authority.

## 1. Release prerequisites

A releasable revision must have:

- exact Git SHA selected;
- Stage 0–6 deterministic regressions green;
- Stage 4 exact-SHA Promotion Gate regression green;
- Black-Islands BI-0/BI-1 green;
- provider live probes green for every provider explicitly configured as `free`;
- tracked-secret scan green;
- repository hygiene green;
- backup/restore and upgrade-copy tests green.

Do not deploy from an unvalidated moving branch head.

## 2. PostgreSQL migration order

On a fresh database:

```text
migrations/0001_stage1_runtime.sql
migrations/0002_provider_request_intent.sql
migrations/stage4/0001_promotion.sql
migrations/stage5/0001_routing_measurements.sql
migrations/stage6/0001_product_surfaces.sql
```

Root Stage 1 migrations may be applied with `PostgresRunRepository.migrate_all`.
Stage-specific migrations are explicit and isolated so older stage proofs do not
silently change meaning.

Production policy:

```text
backup current database
 -> restore/verify backup on a disposable copy
 -> apply new migration on the copy
 -> run release smoke/health checks
 -> apply migration intentionally to production
 -> deploy exact validated application SHA
 -> verify /ready and /v1/system/status
```

Application startup does not silently apply Stage 6 migrations.

## 3. Control Plane launch

Required environment:

```text
AMODEX_DATABASE_URL=<postgresql dsn>
```

Optional promotion execution boundary:

```text
AMODEX_PROMOTION_REPOSITORY=/trusted/git/authority.git
AMODEX_PROMOTION_VALIDATOR="python -m pytest -q"
```

Provider secrets remain deployment secrets, for example:

```text
GEMINI_API_KEY
GROQ_API_KEY
OPENROUTER_API_KEY
NVIDIA_API_KEY
```

Provider connection records should refer to them as `env://...` and never contain
secret material.

Launch example:

```bash
uv run --python 3.13.5 --frozen \
  uvicorn amodex.api.app:app --host 0.0.0.0 --port 8000
```

Verify:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:8000/v1/system/status
```

`/ready` must return 503 if the database/product schema is not ready.

## 4. Termux setup

Install/sync the validated Amodex package in the chosen Termux environment, then:

```bash
export AMODEX_URL=https://<control-plane-host>
amodex system
amodex project add https://github.com/<owner>/<repo>
amodex run "objective" --project <project-id>
amodex status <run-id>
amodex attach <run-id>
```

The terminal can be closed after `amodex run`; execution authority lives remotely.
A later invocation reconstructs state from the API.

## 5. Backup format

`amodex.operations.backup` exports a versioned logical bundle containing only
Amodex-owned authority/projection tables. The bundle includes:

- backup schema version;
- UTC creation timestamp;
- table rows serialized by PostgreSQL `row_to_json`;
- SHA-256 of the canonical table payload.

Environment variables and raw provider keys are not database rows and therefore
are not included.

Example trusted maintenance snippet:

```python
from pathlib import Path
from amodex.operations.backup import export_backup

bundle = export_backup("postgresql://...")
Path("amodex-backup.json").write_text(bundle.to_json(), encoding="utf-8")
```

Store backup files outside the repository in the deployment/provider's protected
backup storage.

## 6. Restore procedure

Never restore blindly over a live database.

1. Create a disposable PostgreSQL database.
2. Apply the same schema migrations as the target release.
3. Load the backup bundle.
4. Verify its SHA-256 before inserts.
5. Restore into the disposable database.
6. Verify per-table row counts.
7. Start the Control Plane against the restored copy.
8. Verify `/ready`, `/v1/system/status`, projects/runs/audit and Candidate/Promotion projections.
9. Only then schedule a production restore with explicit downtime/owner approval.

Example:

```python
from pathlib import Path
from amodex.operations.backup import BackupBundle, restore_backup

bundle = BackupBundle.from_json(Path("amodex-backup.json").read_text())
restore_backup(
    "postgresql://.../restored-copy",
    bundle,
    allow_destructive_restore=True,
)
```

`allow_destructive_restore=True` is intentionally explicit because even a fresh
Stage 6 schema contains seeded policy rows.

## 7. Upgrade verification

Before each schema-changing release, repeat the CI pattern:

```text
create disposable DB
 -> migrate only through currently deployed stage
 -> seed/copy representative durable state
 -> apply new migration
 -> verify old runs/events remain readable
 -> create new-version state
 -> run API health/status tests
```

Stage 6 CI specifically proves upgrading a Stage 1/4/5 copy while preserving an
existing run, then creating a project-linked Stage 6 run.

## 8. Pause/cancel recovery guidance

`PAUSED` means no new governed provider side effect should begin. Resume returns a
paused run to `QUEUED`.

`CANCELLED` is terminal for new work. If cancellation arrives after a provider
request has already started, do not issue a duplicate/replacement request. The
worker persists/reconciles the response and records that cancellation arrived
while the request was in flight.

If a run is `UNKNOWN_OUTCOME`:

1. inspect its durable event timeline;
2. inspect provider request key/state;
3. do not manually reset it to `QUEUED`;
4. use the recovery/reconciliation path appropriate to that provider/side effect;
5. record any operator decision in the audit trail.

## 9. Provider outage/quota recovery

`WAITING_PROVIDER` is a normal visible state. Check `/v1/dashboard` provider
availability and quota reason. Do not add paid routing just to clear the queue.

A provider key being present is not evidence that billing class is free. Billing
classification remains explicit configuration.

## 10. Retention

Stage 6 seeds a conservative policy:

```json
{"terminal_run_days":30,"audit_days":90}
```

`GET /v1/operations/retention/preview?days=N` shows how many terminal runs/events
meet the age rule. Stage 6 intentionally does not expose a one-click destructive
retention endpoint. Destructive cleanup requires a later approved operator job
with backup evidence.

## 11. Rollback

Application rollback:

- deploy the previously validated application SHA only if its schema remains
  compatible with the current database;
- never roll database schema backward by deleting columns/tables ad hoc;
- restore a verified backup to a copy first if schema rollback is required.

Repository/code Promotion rollback continues to use a new Git revert commit through
the Stage 4 lifecycle, never a force-reset of validated history.

## 12. Incident minimum evidence

For a product incident capture:

- release SHA;
- `/v1/system/status` without secrets;
- affected run/candidate/promotion IDs;
- relevant event/audit timeline;
- provider availability reason/health;
- Runner heartbeat state;
- database migration versions;
- backup identifier/hash if recovery is attempted.

Do not paste raw API keys, database passwords, provider request payload secrets, or
private chain-of-thought into incident records.
