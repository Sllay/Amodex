# Amodex Runner and MCP Protocol

Status: **Normative architecture contract — Part 2/3**

This document defines the lifecycle and protocol between the Control Plane and disposable execution runners, including MCP discovery/approval, project bootstrap, artifact capture, Godot/Serena integration, and backend portability.

The central design rule is:

> A Runner executes bounded work; it never becomes the durable brain of Amodex.

---

# 1. Runner responsibilities

A Runner may:

- clone/fetch a project repository;
- create task worktree/branch checkout;
- bootstrap the approved project toolchain;
- start Hermes worker runtime;
- start approved MCP servers;
- start language servers/editor bridges;
- execute bounded shell/file/git/test operations;
- capture logs/screenshots/artifacts;
- create local commits and push task checkpoints when authorized;
- report heartbeats/status/evidence.

A Runner must not own:

- global task DAG;
- paid-resource authorization;
- provider quotas;
- long-lived secrets;
- promotion authority;
- final validation truth;
- project-wide file ownership;
- permanent agent memory as sole copy.

---

# 2. RunnerBackend interface

Backends normalize hosting-specific mechanics.

Conceptual interface:

```text
RunnerBackend
  validate_spec(spec) -> validation result
  create(spec) -> backend instance ref
  start(instance) -> status
  inspect(instance) -> status/metadata
  signal(instance, signal) -> accepted/result
  collect_artifacts(instance) -> artifact refs
  terminate(instance, reason) -> result
  reconcile(backend_instance_id) -> observed state
```

Optional backend capabilities:

```text
attach_logs
exec_control_command
snapshot_cache
stream_status
retrieve_resource_metrics
```

Task logic must not call Northflank/GitHub Actions APIs directly.

---

# 3. Initial backends

## 3.1 Northflank RunnerBackend

Primary candidate for always-free personal deployment.

Use Northflank jobs as on-demand runner execution rather than keeping Godot/Hermes workers permanently alive.

The backend adapter owns:

- job/run creation;
- environment/secret injection;
- status polling/webhook reconciliation;
- job timeout;
- logs/artifact collection path;
- termination;
- mapping backend run ID to Amodex runner ID.

Northflank-specific IDs never become task identity.

## 3.2 GitHub Actions RunnerBackend

Potential fallback for selected CI/validation workloads.

Best suited for:

- reproducible repository checks;
- test matrices;
- artifact/screenshot capture where environment works;
- public-repo free execution or private-repo included quota when policy allows.

Not preferred as the only interactive worker runtime because task lifecycle/control is less direct than a purpose-built runner job.

## 3.3 LocalContainer RunnerBackend

Required for development and fault-injection.

Provides:

- Docker/Podman-like local disposable environment where available;
- deterministic integration tests;
- no external hosting requirement for control-plane development.

The Amodex product does not require the end user to run local models.

---

# 4. Runner profiles

A profile describes capabilities/resource shape, not a specific host.

Initial profiles:

## `GENERAL_CODE`

For:

- Python/JS/general repository tasks;
- Git;
- shell/files;
- tests/linters;
- Hermes;
- lightweight MCP.

## `RESEARCH`

For:

- web/repository read work;
- minimal or no write capabilities;
- no heavy project bootstrap unless needed.

## `GODOT_HEADLESS`

Adds:

- project-pinned Godot;
- Node/runtime for Godot MCP;
- Serena/GDScript LSP;
- headless import/open/tests;
- project addons/dependencies.

## `GODOT_VISUAL`

Adds:

- virtual display (Xvfb or backend equivalent);
- software rendering fallback such as Mesa/llvmpipe where required;
- Godot editor bridge;
- runtime input/screenshot/profiling;
- larger memory/CPU request.

## `INTEGRATION_HEAVY`

Reserved for future project-specific builds requiring larger dependency/toolchain footprint.

Profiles can share one physical Northflank job definition if environment image/bootstrap supports them safely. More logical profiles do not imply more permanent services.

---

# 5. Runner lifecycle

Normative lifecycle:

```text
REQUESTED
   |
   v
PROVISIONING
   |
   v
BOOTSTRAPPING
   |
   v
READY
   |
   v
BUSY
   |
   +--> DRAINING
   |
   v
TERMINATING
   |
   v
TERMINATED
```

Failure states:

```text
FAILED
LOST
QUARANTINED
```

`QUARANTINED` means suspicious/security-invalid state; do not reuse/cache it.

---

# 6. Runner bootstrap phases

Bootstrap is explicit and evidence-bearing.

## Phase A — base image verification

Record:

- image digest;
- OS/arch;
- base tool versions;
- runner agent version.

## Phase B — repository checkout

- authenticate with short-lived scoped token;
- fetch required refs;
- verify expected repository identity;
- checkout expected SHA/task branch;
- canonicalize workspace path.

## Phase C — project trust/bootstrap

- load approved bootstrap profile;
- verify trusted script/package digests;
- install project toolchain/dependencies;
- never silently run changed bootstrap content.

## Phase D — capability services

Start only requested services:

- Hermes worker;
- Serena/LSP;
- Godot MCP;
- virtual display;
- test services.

## Phase E — probes

Each requested capability must pass readiness probe before task starts.

---

# 7. Runner control protocol

The runner agent exposes a narrow authenticated control channel to Control Plane.

Conceptual commands:

```text
HEARTBEAT
ASSIGN_TASK_PACKAGE
REVOKE_ATTEMPT
INTERRUPT
DRAIN
COLLECT_ARTIFACTS
TERMINATE
STATUS
```

Runner does **not** expose unrestricted remote shell to the public internet.

A separate owner/admin debug path may exist later but is not part of normal task protocol.

---

# 8. Heartbeat payload

```yaml
runner_id: <uuid>
attempt_id: <uuid-or-null>
fencing_token: 119
state: BUSY
observed_at: ...
resources:
  cpu_percent: 48.2
  memory_mb: 1230
  disk_mb: 4100
process_health:
  worker: healthy
  mcp:
    project.godot: healthy
  lsp:
    gdscript: healthy
workspace:
  branch: task/...
  head_sha: ...
```

Resource numbers are telemetry, not trusted acceptance evidence.

---

# 9. Environment image strategy

Avoid rebuilding the whole world per task when a reusable image can pin common dependencies.

Recommended layering:

```text
amodex-runner-base
  -> Python/runtime
  -> Git/basic tools
  -> Hermes runtime
  -> runner agent

amodex-runner-godot
  -> base
  -> Node 22+
  -> Xvfb/Mesa tools
  -> bootstrap prerequisites
```

Project-specific Godot version/addons remain pinned by project contract when practical rather than baking every project into one image.

Images must use immutable digest in validated deployments.

---

# 10. Caching policy

Safe caches may include:

- package-manager caches;
- Godot download cache;
- dependency archives by checksum;
- import cache only when project correctness rules permit;
- model-independent code index caches keyed by repository SHA.

Never rely on cache as sole durable state.

Cache keys include versions/digests to prevent cross-project poisoning.

A cache miss must be slower, not semantically different.

---

# 11. Workspace layout

Conceptual:

```text
/work/amodex/
  workspace/       task Git worktree
  artifacts/       outgoing artifacts
  logs/            bounded local logs
  cache/           disposable/reusable cache
  runtime/         sockets/PIDs/local service state
```

Only `workspace/` paths within granted scope are writable by repository-edit tools.

MCP/project tools may require broader project-root access but remain container-contained and capability-governed.

---

# 12. MCP lifecycle

For each approved MCP server:

```text
DECLARED
  -> APPROVED
  -> STARTING
  -> PROBING
  -> READY
  -> BUSY/READY
  -> STOPPING
  -> STOPPED
```

Failure:

```text
FAILED
QUARANTINED
```

The runner reports MCP instance identity/version/fingerprint.

---

# 13. MCP configuration ingestion

Repository config examples may come from:

- `.codex/config.toml`;
- `.mcp.json`;
- project-specific config;
- Amodex project contract.

Ingestion extracts declarations:

- server name;
- executable;
- args;
- working directory;
- env names;
- transport;
- timeouts.

Then security policy decides whether the declaration matches an approved server profile.

No automatic execution during indexing.

---

# 14. MCP capability mapping

Raw tool names are mapped to semantic capabilities.

Example:

```text
godot_status              -> godot.status
godot_run_project         -> godot.runtime.run
godot_screenshot          -> godot.runtime.screenshot
get_symbols               -> gdscript.symbols
rename_symbol             -> gdscript.symbol.rename
```

Mapping contains:

- risk class;
- side-effect class;
- required scope;
- evidence eligibility;
- timeout;
- whether deterministic validator may call it.

Unknown raw tools remain disabled until mapped/approved.

---

# 15. Hermes MCP integration

Hermes supports MCP tool discovery/filtering. Amodex can reuse this transport/runtime integration, but must place an authorization layer around discovery and dispatch.

Expected flow:

```text
approved MCP process
  -> Hermes discovers tool schemas
  -> Amodex adapter maps allowed tools to CapabilityDescriptor
  -> TaskPackage grants subset
  -> worker/model sees only granted subset
  -> dispatch wrapper rechecks grant/fencing
```

Do not expose all discovered MCP tools to every worker.

---

# 16. Godot MCP topology

Recommended runner-local topology:

```text
Hermes worker
      |
      | MCP stdio/local
      v
Godot MCP server
      |
      | loopback editor bridge
      v
Godot editor / game runtime
      |
      +--> GDScript LSP
      +--> DAP/debug
      +--> screenshot/input/profile
```

Godot MCP currently supports live editor bridge plus headless fallback, runtime input, screenshots, profiling, debugger and GUT/GdUnit4 test operations for supported Godot versions. These capabilities must still be verified by project-specific probe before reliance. 

---

# 17. Serena topology

For GDScript projects such as Black-Islands:

```text
worker
  |
  +--> Serena MCP
          |
          v
       Godot GDScript LSP
```

Preferred responsibility:

- symbol search;
- reference lookup;
- semantic rename;
- focused symbol edit/navigation.

Godot MCP responsibility:

- scene/resource/project operations;
- diagnostics/engine state;
- runtime/editor inspection;
- tests/debug/profiling/screenshots.

Avoid redundant whole-file reading when Serena can target symbols.

---

# 18. Godot LSP ownership

Only one intended project/editor instance should own the configured LSP port/session for a runner workspace.

Runner bootstrap must detect:

- port conflict;
- wrong project listening;
- stale process;
- editor bridge mismatch.

Do not reuse a random existing Godot LSP process from another workspace.

---

# 19. Headless validation protocol

Typical sequence:

```text
checkout
 -> bootstrap project tools
 -> Godot version check
 -> import/headless editor open
 -> LSP readiness
 -> Serena probe
 -> Godot MCP status/headless probe
 -> execute focused tests/diagnostics
 -> capture logs/evidence
```

Headless pass never claims visual/mobile performance.

---

# 20. Visual validation protocol

Visual profile sequence:

```text
virtual display start
 -> rendering environment probe
 -> Godot editor/bridge start
 -> project launch
 -> wait for deterministic readiness condition
 -> perform bounded input sequence
 -> capture screenshot/runtime state/logs
 -> validator/vision analysis
 -> stop project/editor cleanly
 -> collect artifacts
```

Readiness should use deterministic state when possible rather than arbitrary `sleep 5`.

Examples:

- expected scene/node exists;
- runtime bridge reports running;
- no project errors;
- UI signal/state exposed by test harness.

Timeout remains as fail-safe.

---

# 21. Screenshot evidence

A screenshot artifact includes:

- exact commit SHA;
- viewport/window dimensions;
- render backend when known;
- runtime scene/test ID;
- input scenario ID;
- capture timestamp;
- image SHA-256;
- environment fingerprint.

Visual LLM review adds a separate evidence record with model/provider/provenance.

Screenshot itself does not prove behavior that is not visible.

---

# 22. Runtime input

Input scenarios are versioned artifacts/scripts, not vague model prose.

Conceptual scenario:

```yaml
id: inventory-open-drag-v1
steps:
  - action: tap
    target: inventory_button
  - wait_for: ui.inventory.visible
  - action: drag
    from: slot_1
    to: slot_4
  - assert_runtime: inventory_revision_changed
  - screenshot: inventory-after-drag
```

The actual implementation may use Godot MCP input primitives/test harness coordinates or semantic runtime hooks.

---

# 23. Artifact collection

Runner uploads/returns artifacts before termination when required.

Artifact classes:

- validator logs;
- screenshots;
- profiling data;
- test reports;
- crash dumps;
- environment manifest;
- diff summary.

Artifacts are bounded by size/retention policy.

Secret redaction occurs before normal upload.

---

# 24. Runner teardown

Before normal termination:

1. stop accepting new task calls;
2. ensure required checkpoint is remotely durable;
3. collect required artifacts;
4. terminate project/editor/MCP processes;
5. flush runner status;
6. revoke short-lived credentials;
7. backend terminate;
8. mark runner terminated after backend observation.

If forced termination happens earlier, recovery coordinator handles missing steps.

---

# 25. Resource scheduling

Scheduler chooses profile/backend based on:

- required capabilities;
- minimum memory/CPU;
- free capacity;
- platform compatibility;
- job limit;
- startup cost/time;
- artifact requirements.

Do not launch visual Godot profile for a Markdown-only task.

---

# 26. Northflank topology under Sandbox constraints

Initial preferred mapping while current free limits permit:

```text
SERVICE 1: Amodex Control Plane/API
SERVICE 2: reserved/optional auxiliary service
ADDON 1: PostgreSQL
JOB 1: General Runner
JOB 2: Heavy/Visual Runner
```

Logical runner profiles share jobs/images when feasible.

If free compute cannot support Godot visual workloads, keep Control Plane on Northflank and route that profile to another zero-cost-compatible backend.

No architecture component assumes all profiles must live on Northflank.

---

# 27. POC-INFRA-001 — Godot on free Northflank

Mandatory before selecting Northflank for Godot visual execution.

Use a controlled Black-Islands clone/test branch or equivalent fixture without modifying production repository.

Prove:

1. runner job launches;
2. exact Godot 4.7.2 installs/starts;
3. project bootstrap completes within job limits;
4. headless import/open succeeds;
5. Serena starts and resolves a real GDScript symbol;
6. Godot MCP starts and `godot_status` works;
7. virtual display starts;
8. real game/test scene launches;
9. runtime input works;
10. screenshot captured;
11. project diagnostics captured;
12. process cleanup works;
13. peak RAM measured;
14. CPU/startup time measured;
15. artifact upload/collection succeeds;
16. runner termination/recreate succeeds.

Pass criteria are not “command exited zero”. Evidence must demonstrate each capability.

---

# 28. POC-INFRA-002 — recovery

Prove runner disposability:

1. start task;
2. push SAFE checkpoint;
3. kill backend job;
4. expire/reconcile lease;
5. create replacement job;
6. checkout checkpoint;
7. recreate MCP/LSP environment;
8. continue and validate.

---

# 29. POC-MCP-001 — capability filtering

Use an MCP fixture advertising:

- one allowed tool;
- one unknown tool;
- one dangerous tool.

Prove:

- discovery sees all;
- worker only receives granted allowed capability;
- unknown/dangerous calls fail at dispatch even if model fabricates raw tool name.

---

# 30. POC-GODOT-001 — scoped real project flow

For Black-Islands-style project:

- ingest `.codex/config.toml` declaration;
- approve pinned Godot MCP/Serena profiles;
- start toolchain;
- locate one GDScript symbol with Serena;
- execute a no-source-change Godot status/smoke;
- capture one screenshot in visual profile;
- produce environment/evidence record;
- terminate cleanly.

No production code changes are required for this PoC.

---

# 31. Runner error taxonomy

Canonical classes:

- `PROVISION_FAILED`;
- `BOOTSTRAP_FAILED`;
- `REPOSITORY_CHECKOUT_FAILED`;
- `CAPABILITY_PROBE_FAILED`;
- `RESOURCE_EXHAUSTED`;
- `TOOLCHAIN_MISMATCH`;
- `MCP_START_FAILED`;
- `MCP_CRASHED`;
- `LSP_UNAVAILABLE`;
- `DISPLAY_FAILED`;
- `ARTIFACT_UPLOAD_FAILED`;
- `BACKEND_TIMEOUT`;
- `BACKEND_LOST`;
- `SECURITY_QUARANTINE`.

Each has retry policy; repeated bootstrap failures with same fingerprint escalate instead of relaunching indefinitely.

---

# 32. Backend portability tests

A backend implementation must pass a common conformance suite:

1. create/start/inspect/terminate;
2. environment injection without leaking secret values;
3. timeout enforcement;
4. lost-run reconciliation;
5. artifact collection;
6. distinct concurrent runner identities;
7. runner ID mapping survives Control Plane restart;
8. termination idempotency/reconciliation;
9. resource/profile rejection reports structured reason;
10. zero-cost policy can block backend resource choice.

---

# 33. No fake runner integration

Implementation stage cannot claim a RunnerBackend is complete if it only:

- returns mock `READY`;
- shells out locally while labeled Northflank;
- accepts `RunnerSpec` but ignores resource/capability fields;
- stores screenshots generated outside the actual runner;
- never tests termination/recovery.

The vertical slice must use an actual backend before that backend is marked integrated.

Mocks remain valid for unit tests only.

---

# 34. Frozen Part 2 decisions

1. RunnerBackend isolates task logic from host APIs.
2. Logical runner profiles are capability/resource profiles, not permanent services.
3. Runner filesystem is disposable and non-authoritative.
4. Bootstrap executes only approved project/toolchain content.
5. Capability probes are mandatory before task execution.
6. MCP declarations are parsed separately from trust/authorization.
7. Discovered MCP tools are filtered/mapped before model exposure.
8. Godot MCP and Serena run close to the workspace inside runner.
9. Godot editor/LSP/debug bridges are not public internet services.
10. Headless and visual validation are distinct profiles.
11. Visual evidence is tied to exact runtime scenario/environment/SHA.
12. Northflank is initial host candidate, never architectural dependency.
13. Godot visual feasibility on free Northflank remains a mandatory PoC.
14. Backend conformance and forced runner recovery are release gates.
15. A backend is not considered integrated until exercised end-to-end outside mocks.
