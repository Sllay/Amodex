# Post-Baseline Environment Product & Runners V1

Status: implementation slice after the completed Stage 0–6 baseline.

This is **not** a new fictional Stage 7. It extends the validated post-baseline
Environment isolation and quota-budget architecture with daily-use product surfaces
and scoped execution runners.

## 1. Purpose

Amodex can now expose long-lived isolated Environments through the same Control Plane
used by the Stage 6 CLI/Web product and can create a disposable execution job whose
authority is bound to one Environment, one Session, one Run and one Project.

The important invariant is unchanged:

```text
Owner / Control Plane
  |
  +-- Environment A
  |     +-- Project A
  |     +-- Session A1
  |     +-- Run A1
  |     +-- Runner Job A1 (scoped)
  |
  +-- Environment B
        +-- Project B
        +-- Session B1
        +-- Run B1
        +-- Runner Job B1 (scoped)
```

A runner cannot discover or borrow authority from a sibling Environment.

## 2. Product surfaces

### CLI

The existing `amodex` CLI remains stateless and talks only to the Control Plane API.
New commands include:

```bash
amodex env create "Black Island" --engine godot
amodex env list
amodex env show <environment-id>
amodex env use <environment-id>
amodex env bind-project <environment-id> <project-id>

amodex env session create <environment-id> "Inventory work" --domain gameplay
amodex env session list <environment-id>

amodex run "Implement inventory validation" \
  --project <project-id> \
  --environment <environment-id> \
  --session <session-id>

amodex env usage <environment-id>
amodex env jobs <environment-id>

amodex env dispatch <environment-id> <run-id> <session-id> \
  --profile implementation \
  --task-class ROUTINE \
  --write src/inventory.gd
```

`env use` does not create hidden local authority. It validates the Environment and
prints shell export guidance for `AMODEX_ENVIRONMENT_ID`. The user may explicitly set
`AMODEX_SESSION_ID` in the same shell when desired.

### Web

The Stage 6 dependency-free dashboard now projects:

- Environments;
- Environment Sessions;
- per-Environment provider/model token usage;
- Environment runner jobs and lifecycle;
- the existing projects, runs, task DAG, provider status, runner heartbeats,
  approvals, checkpoints, evidence and audit timeline.

The browser still owns no durable state and no repository/provider authority.

## 3. Durable runner lifecycle

Runner jobs use this lifecycle:

```text
QUEUED
  |-- GitHub dispatch accepted --> DISPATCHED
  |-- dispatch outcome ambiguous --> DISPATCH_UNKNOWN
  |-- Desktop runner polls/claims --> CLAIMED

DISPATCHED / DISPATCH_UNKNOWN
  --> CLAIMED --> RUNNING --> COMPLETED | FAILED | CANCELLED
```

`DISPATCH_UNKNOWN` is deliberate. `workflow_dispatch` is an external side effect: if
the HTTP call times out after GitHub accepted it, Amodex cannot safely infer that no
runner was created. Therefore the Control Plane **does not blindly redispatch** an
ambiguous job. If the original GitHub runner actually started, it can still claim that
same durable `job_id`.

## 4. GitHub Actions disposable runner

`.github/workflows/environment-runner.yml` is the current headless runner.

The workflow receives exactly one public workflow input:

```text
job_id
```

It then authenticates to the Control Plane and claims that exact durable job. The
Control Plane returns the scoped project repository, Environment identity, engine,
Run objective and capability set.

The workflow does **not** receive Gemini, Groq, OpenRouter or NVIDIA provider keys.
Model credentials remain a Control Plane concern.

For a scoped project the runner:

1. claims the exact job;
2. validates that the job is `ACTIONS_HEADLESS`;
3. accepts only scoped `https://github.com/owner/repo` repositories;
4. clones the project into `$RUNNER_TEMP`;
5. marks the job `RUNNING`;
6. reconstructs and validates the engine-specific workspace;
7. reports evidence and a terminal state;
8. exits and loses its workspace.

V1 intentionally performs no project push. Repository mutation/promotion authority
remains separate from runner reconstruction/validation.

### Private repository clone credential

`AMODEX_PROJECTS_TOKEN` is optional. When configured it exists only in the clone step
and is consumed through temporary `GIT_ASKPASS`; it is not placed in the job-wide
environment and is not persisted into Git config.

## 5. Godot Work Environment

For `engine=godot`, the Control Plane maps the job to `ACTIONS_HEADLESS` and grants
only the declared Godot headless capabilities.

The runner currently:

- installs the pinned Godot 4.7.2 binary;
- validates the project through `godot --headless --editor`;
- runs `tests/headless_test.gd` when the project supplies it;
- creates a disposable project copy for MCP probing;
- installs the pinned `@shin2344234/godot-mcp@0.1.2` addon into that copy;
- requires the MCP headless scene backend to be available;
- reports the source SHA and engine/MCP evidence to the Control Plane.

The MCP copy is deliberately disposable so addon/cache files never modify the tracked
project merely because validation ran.

## 6. Roblox Studio Environment

Roblox Studio is **not** mapped to the Linux GitHub Actions runner.

Current official Roblox Studio MCP documentation describes a local MCP server that
runs with Roblox Studio on Windows or macOS over stdio. Amodex therefore maps
`engine=roblox` / `Roblox Studio` to `DESKTOP_STUDIO` and leaves the durable job queued
for a compatible Desktop Runner.

Official references:

- https://create.roblox.com/docs/studio/mcp
- https://github.com/Roblox/roblox-studio-agent-plugin

Documented local executable locations are represented by the Desktop Runner boundary:

- Windows: `%LOCALAPPDATA%\Roblox\mcp.bat`
- macOS: `/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP`

Linux fails closed instead of pretending an interactive Studio session exists.

## 7. Control Plane configuration

For GitHub Actions dispatch from the Control Plane:

```text
AMODEX_GITHUB_DISPATCH_REPOSITORY=owner/Amodex
AMODEX_GITHUB_DISPATCH_TOKEN=<trusted GitHub dispatch credential>
AMODEX_GITHUB_DISPATCH_WORKFLOW=environment-runner.yml   # optional
AMODEX_GITHUB_DISPATCH_REF=main                          # optional
AMODEX_RUNNER_CONTROL_TOKEN=<random runner-control secret>
```

`AMODEX_GITHUB_DISPATCH_TOKEN` belongs only to the trusted Control Plane. It is not
passed to project runners.

The post-baseline database migrations must be applied after Stage 6:

```text
migrations/postbaseline/0001_environment_isolation_budget.sql
migrations/postbaseline/0002_environment_product_runners.sql
```

Stage 6 `/ready` compatibility is intentionally retained. Environment endpoints fail
with a recoverable `503` if the post-baseline migrations are missing rather than
making old Stage 6 health checks lie about the reason.

## 8. GitHub Actions repository secrets

The operational `environment-runner` workflow expects:

```text
AMODEX_CONTROL_PLANE_URL
AMODEX_RUNNER_CONTROL_TOKEN
AMODEX_PROJECTS_TOKEN   # optional; only needed for private target repositories
```

The first two connect a disposable runner to the Control Plane. The optional project
token is only a repository-read credential for the clone operation.

Provider secrets such as these must **not** be configured as dependencies of the
project runner workflow:

```text
GEMINI_API_KEY
GROQ_API_KEY
OPENROUTER_API_KEY
NVIDIA_API_KEY
```

They remain server-side provider connections governed by billing, health, lifecycle,
quota and Environment budgets.

## 9. Token / quota telemetry

The product layer reuses `amodex_environment_usage_events`; it does not invent a
second accounting system.

`GET /v1/environments/{environment_id}/usage` and the dashboard aggregate:

- input tokens;
- output tokens;
- request count;
- provider;
- model;
- event count.

This is the basis for measuring real work-per-token once production-eligible model
routes are explicitly promoted.

## 10. AI execution gate remains deliberate

This slice makes execution infrastructure operational, but it does **not** silently
promote Stage 5 model candidates from `BENCHMARK_PENDING` merely because live provider
smoke tests succeeded.

Connectivity and free-billing smoke tests prove that an API path works. They do not
prove coding quality, correction reliability, structured-output compliance, research
quality, review quality, vision quality or long-context behavior.

Before autonomous code-writing jobs use the provider pool, Amodex must run the
capability benchmark, persist measurements and explicitly promote eligible routes.
This preserves the rule that a smoke test cannot become production authority by
accident.

## 11. Safety invariants

- one Project belongs to one Environment;
- a Run can bind only to a Session in that Project's Environment;
- runner jobs carry the same Environment/Session/Run/Project tuple durably;
- cross-Environment dispatch fails closed;
- ordinary Environment endpoints cannot write OWNER memory;
- runner claim/report endpoints require a separate bearer credential;
- an ambiguous GitHub dispatch is not blindly retried;
- Roblox Studio jobs are never sent to Linux Actions;
- project runner workflows receive no model provider credentials;
- the current Actions runner does not push project changes;
- model production eligibility still requires measured lifecycle promotion;
- Black-Islands is not written by this slice.

## 12. Current boundary and next slice

After this V1, the infrastructure needed to *run and validate* scoped work is present.
The next logical post-baseline slice is measured model eligibility plus a Control
Plane-mediated inference/execution loop, so the disposable runner can request bounded
AI work without receiving provider credentials and without bypassing the quota/budget
governor.
