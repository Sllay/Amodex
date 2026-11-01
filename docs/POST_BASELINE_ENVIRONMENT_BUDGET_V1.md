# Post-Baseline Environment Isolation & Resource Budget V1

Status: implementation slice after the completed Stage 0-6 baseline.

## 1. Problem

Amodex must be able to work on several long-lived projects at the same time without
turning them into one shared context. A Godot project and a Roblox project may execute
concurrently, but their Directors, workers, repositories, project memories, secrets,
sessions, run history and engine capabilities must not leak into one another.

At the same time, free model quotas are a production resource. A simple code edit must
not consume hundreds of thousands of tokens merely because a large-context model is
available.

This slice establishes both invariants before adding Roblox Cloud runners.

## 2. Environment hierarchy

```text
Owner
  |
  +-- owner memory (preferences/process only)
  |
  +-- Environment: Godot game
  |     +-- project binding
  |     +-- environment memory
  |     +-- domain memory
  |     +-- Session A
  |     |     +-- session memory
  |     |     +-- runs/tasks
  |     +-- Session B
  |
  +-- Environment: Roblox game
        +-- independent project binding
        +-- independent memory
        +-- independent sessions/runs
```

The Control Plane may know every environment because it schedules resources. A Director
or worker receives exactly one `EnvironmentGrant`; it is not authorized to discover
sibling environments.

## 3. Memory layers

V1 supports four explicit scopes:

- `OWNER`: reusable preferences/process rules. No project architecture belongs here.
- `ENVIRONMENT`: durable knowledge for one project environment.
- `DOMAIN`: reusable knowledge for one domain inside one environment, such as maps,
  combat or mobile performance.
- `SESSION`: current work context for one environment session.

Owner memory is the only intentionally cross-environment layer. It is read-only through
ordinary grants. Environment/domain/session memories are never returned across an
environment boundary.

Memory is not a raw transcript. Each durable memory retains a source reference and
content hash so later retrieval can preserve provenance and deduplicate repeated facts.

## 4. Repository, run and secret isolation

A project can be bound to only one Environment.

A run may enter an Environment session only when:

1. the run is project-bound;
2. that project is already bound to the same Environment;
3. the target session belongs to the same Environment.

A mismatched operation fails closed. The new storage layer never silently reassigns
projects or runs.

`EnvironmentGrant` also scopes repository URLs, project IDs, secret references and
capabilities. A worker cannot obtain permission merely because a sibling environment
has a useful credential.

## 5. Resource budgets

Free quota is treated as a schedulable resource.

Default task envelopes:

| Class | Input tokens | Output tokens | Requests | Fallbacks | Provider reserve floor |
| --- | ---: | ---: | ---: | ---: | ---: |
| MICRO | 6,000 | 2,000 | 1 | 1 | 35% |
| ROUTINE | 12,000 | 4,000 | 2 | 1 | 25% |
| STANDARD | 24,000 | 8,000 | 4 | 2 | 10% |
| COMPLEX | 64,000 | 20,000 | 8 | 3 | 3% |
| CRITICAL | 128,000 | 40,000 | 12 | 4 | 0% |

These are hard safety defaults, not a promise that every task should consume the full
amount.

The policy is deliberately asymmetric:

- routine work preserves scarce high-quality routes when a more abundant eligible route
  exists;
- complex work may consume more quota when the task genuinely requires it;
- critical work can use the final eligible free route if necessary;
- exceeding the token envelope requests task splitting/context narrowing instead of
  silently spending more;
- repeated failing routes are excluded and fallback count is bounded;
- a paid route is still ineligible even when it would score higher.

If every free route is unavailable or budget-ineligible, the correct state is blocked/
escalated. Amodex must not secretly spend money or pretend a weak incompatible model is
good enough.

## 6. Provider failure adaptation

Provider availability remains fresh runtime state.

Example:

```text
Groq healthy
OpenRouter healthy
NVIDIA scarce
Gemini healthy
        |
        v
Groq becomes unavailable
        |
        v
router excludes Groq
        |
        v
OpenRouter quota exhausted
        |
        v
routine work preserves scarce NVIDIA when Gemini is sufficient
critical work may use NVIDIA
```

There is no infinite retry loop. Failed route IDs are fenced for that task attempt, and
fallbacks have a task-class ceiling.

## 7. Usage ledger

`amodex_environment_usage_events` records:

- Environment;
- optional session/run/task;
- provider/model;
- input and output token usage;
- request count;
- outcome.

This makes later efficiency metrics possible, including progress per request/token and
provider quota burn by Environment. Usage for a run cannot be recorded against a
different Environment.

## 8. Stress benchmark

`python -m amodex.benchmarks.environment_budget_stress` is deterministic and makes no
live model requests.

It exercises hundreds of tasks across two simultaneous environments while:

- attempting a forbidden sibling access on every task;
- forcing a provider outage;
- forcing a second provider's free quota to disappear;
- keeping a strong provider deliberately scarce;
- including a paid candidate that must never be selected;
- enforcing per-task context/output budgets.

This is intentionally a simulation around the real routing/budget code. Draining real
free-provider quota merely to test quota protection would be self-defeating. Live
provider checks remain small connectivity/contract smokes.

## 9. Godot on GitHub Actions

Amodex already has concrete evidence that its Godot path works on a GitHub-hosted Linux
runner: `.github/workflows/stage0-godot-headless.yml` pins Godot 4.7.2, starts
`--headless --editor`, validates the LSP, imports/boots a real fixture, installs the
pinned Godot MCP addon/server and requires a successful MCP capability probe.

The post-baseline stress workflow reuses that proven pin rather than silently changing
the MCP implementation.

The new workflow adds:

- multiple parallel real Godot headless test executions;
- one real MCP headless capability probe;
- environment/budget stress tests in the same ephemeral runner;
- runner memory/disk/process evidence.

### What headless CI can validate

- GDScript parse/import;
- project boot;
- deterministic engine tests;
- Godot LSP;
- MCP headless capabilities supported by the pinned server;
- scene/file operations exposed by that backend;
- exports that do not require an interactive display.

### What remains display-dependent

Screenshots, visual composition judgement and truly interactive window/input validation
may require a virtual display or a persistent desktop/editor runner. They are not
pretended to pass in headless CI.

## 10. Acceptance invariants for V1

1. sibling Environment project access is rejected;
2. a project cannot be rebound to a second Environment;
3. a run cannot be attached to a session from another Environment;
4. memory retrieval never returns sibling Environment memory;
5. owner preference memory may be shared intentionally;
6. usage cannot be attributed to the wrong Environment;
7. MICRO/ROUTINE work cannot exceed its hard token/request budget silently;
8. scarce free routes are preserved for routine work when alternatives exist;
9. critical work may use scarce eligible free capacity;
10. paid routes remain forbidden by zero-cost routing;
11. provider failures cause bounded failover, not repeated equivalent retries;
12. deterministic stress does not consume live provider quota;
13. Godot headless + MCP capability remains validated on an ephemeral GitHub runner.

## 11. Next slice

After this V1 is green, the next implementation should expose Environments/Sessions in
the API/CLI/Web and issue environment-bound ephemeral GitHub Actions grants. Roblox
Open Cloud integration should bind to that Environment contract instead of inventing a
parallel project/session system.
