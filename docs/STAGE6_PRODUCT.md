# Stage 6 — Product surfaces and daily-use operations

Status: **Stage 6 implementation contract**

Stage 6 exposes the already-governed Amodex runtime as a daily-use product. It
does not move orchestration, provider, Git, scope, validation, or promotion
authority into Termux or the web browser.

## One authority path

```text
Termux CLI ─┐
            ├──> Control Plane API ──> durable PostgreSQL authority
Web UI ─────┘             │
                          ├──> Orchestrator / worker controls
                          ├──> Provider Governor projections
                          └──> Stage 4 Promotion Gate
```

The web page contains no separate project/run state. It reloads `/v1/dashboard`
and invokes the same action endpoints used by the CLI.

## Termux commands

The installed `amodex` entry point supports the normative workflow:

```text
amodex project add <repo>
amodex run "objective" --project <project-id>
amodex status <run-id>
amodex attach <run-id>
amodex pause <run-id>
amodex resume <run-id>
amodex cancel <run-id>
amodex approve <approval-id>
amodex promote <candidate-id>
```

Additional useful operations:

```text
amodex project list
amodex provider connect <provider> env://SECRET_VAR --pool <pool> --billing free
amodex provider list
amodex system
amodex dashboard
```

`AMODEX_URL` may select the remote Control Plane. No run state is authoritative
on the phone. Every invocation can be a fresh process.

## Disconnect/reconnect semantics

Creating a run returns a durable `run_id`. The CLI process may exit immediately.
A later `status` or `attach` reconstructs state from the API/database.

`attach` polls durable events. Killing `attach` kills only the client process; it
never owns or cancels the remote worker.

## Pause and cancellation

Pause/cancel are cooperative, durable control intents.

- queued/waiting work can move to `PAUSED`/`CANCELLED` immediately;
- the worker checks control state before provider side effects and again directly
  before a provider request;
- a paused run is excluded from new worker claims;
- resume moves a paused run back to `QUEUED`;
- cancellation arriving after an external request already started cannot undo
  that request. The provider result is durably reconciled and the run terminates
  `CANCELLED` with an event explaining that the external request had already been
  in flight.

This is intentionally safer than pretending an HTTP inference request can always
be revoked after transmission.

## Web surface

`GET /` serves a dependency-free responsive dashboard. It projects:

- projects;
- runs;
- task DAG/status;
- provider availability/quota and connection state;
- Runner status/heartbeats;
- checkpoints;
- Candidates and Promotions;
- validation evidence/artifact hashes;
- approvals and blockers;
- combined run/audit timeline;
- zero-cost/release state.

Stage 6 deliberately does not add a code editor or browser IDE.

## Project configuration

`POST /v1/projects` records repository identity. `project_id` is optional on the
legacy Stage 1 run contract and is populated by Stage 6 clients.

Adding a project does not grant a worker permission to write it. TaskPackage,
ownership, validation, and Promotion Gate rules remain unchanged.

## Provider connection flow

The API rejects raw provider secret values. Connections accept only secret
references of the form:

```text
env://GROQ_API_KEY
env://OPENROUTER_API_KEY
env://NVIDIA_API_KEY
env://GEMINI_API_KEY
```

The database stores provider, credential-pool ID, secret reference, billing class,
and enabled state. It never stores the environment value.

`billing_class=free` is still explicit owner policy. Key presence alone never
means free or authorized.

## Health and readiness

- `GET /health`: process/database liveness projection.
- `GET /ready`: product schema/database readiness; fails closed with HTTP 503.
- `GET /v1/system/status`: release, schema, settings and visible zero-cost state.

## Approvals and audit

Approvals include action, subject, impact, status and expiry. Owner approval and
rejection are explicit API operations.

Operator actions append `amodex_audit_events`. The dashboard and `/v1/audit`
provide an accessible history without hidden chain-of-thought.

## Runner status

Runners heartbeat through `/v1/runners/{runner_id}/heartbeat`. Stale active
heartbeats are projected as `OFFLINE`; the browser does not infer a healthy
Runner merely because a hosting platform container exists.

## Task/checkpoint/evidence projection

Stage 6 projection tables make deterministic Director/Orchestrator and validation
state visible to clients. They do not replace the canonical contracts or grant
new authority. Candidate/Promotion data is read directly from Stage 4 tables.

## Promotion

`POST /v1/candidates/{candidate_id}/promote` is only enabled on a trusted Control
Plane configured with:

```text
AMODEX_PROMOTION_REPOSITORY=/trusted/path/to/bare-or-local-git-authority
AMODEX_PROMOTION_VALIDATOR="<deterministic validator command>"
```

The endpoint calls the existing Stage 4 `PromotionGate`. Product clients never
receive direct ref-update credentials.

## Retention

The Stage 6 migration seeds an explicit retention policy. The API currently
exposes a read-only retention preview so an operator can see exactly how many
terminal runs/events would be affected before a future/destructive cleanup job is
approved. No request path silently deletes history.

## Daily-use safety invariants

1. CLI/web own no durable authority.
2. raw secrets are rejected by product configuration endpoints.
3. zero-cost state is visible and remains fail-closed.
4. pause/cancel are durable and reconcile external side effects honestly.
5. promotion still crosses the Stage 4 exact-SHA gate.
6. Runner health comes from Amodex heartbeat state.
7. audit/event timelines explain state without raw logs or chain-of-thought.
8. product migrations are isolated under `migrations/stage6/`.
9. backup/restore and upgrade procedures are verified against PostgreSQL copies.
10. Black-Islands BI-0/BI-1 must remain green.

Operational backup, restore, upgrade, deployment and recovery procedures are in
`docs/STAGE6_OPERATIONS.md`.
