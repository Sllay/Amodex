# P0-02 — Hermes Embedding and Authority Isolation

Status: **ADAPT**

P0-02 proves that the pinned Hermes Agent runtime can serve as a bounded Amodex worker-runtime substrate, but it should not be installed into or treated as the Amodex Control Plane itself.

## Upstream under test

Repository: `NousResearch/hermes-agent`

Pinned commit:

```text
adecc4024456b387b1518813b90bd60c82b51287
```

License observed at that commit: **MIT**.

## Decision summary

**ADAPT.**

Amodex will use Hermes through an isolated worker runtime/process/container with a thin compatibility adapter. The Control Plane remains independent and keeps authority over:

- task/run state;
- provider/model selection;
- Context Capsule / task-specific system overlay;
- exposed toolsets/capabilities;
- delegation permission;
- cancellation/lease revocation;
- durable authority and recovery;
- Git ownership/checkpoints/promotion;
- cost policy.

Hermes supplies the worker-local agent loop, provider/tool execution machinery, plugin/MCP registration, and observer seams.

## Why this is ADAPT rather than direct ADOPT

The authority boundary passed, but the runtime has a deliberately large and fast-moving environment:

- Hermes bootstrap selected Python `3.14.9`;
- Pydantic `2.14.1`;
- OpenAI SDK `2.54.0`;
- MCP `2.3.0`;
- `uv 0.13.5`;
- broad runtime/tool dependencies;
- separate browser/CUA/tool assets.

The Amodex Control Plane P0-01 is pinned independently to Python `3.13.5`, Pydantic `2.13.5`, pytest `9.0.2`, and uv `0.10.0`.

Mixing these dependency graphs into one process/venv would make Amodex inherit Hermes dependency churn and would weaken recovery/isolation boundaries. Therefore the supported architecture is:

```text
Amodex Control Plane environment
        |
        | bounded worker protocol
        v
Hermes Worker Runtime environment
        |
        +-- AIAgent
        +-- approved toolsets
        +-- approved plugins/MCP
        +-- provider route supplied by Amodex
```

Programmatic embedding still occurs **inside the Hermes worker process**; the isolation decision is about process/environment authority, not rejecting the Python API.

## Zero-cost probe design

The successful probe spent no model/API credit.

It used:

- exact Hermes source checkout;
- temporary `HOME`, `HERMES_HOME`, working directory and Amodex durable directory;
- a loopback `ThreadingHTTPServer` implementing OpenAI-compatible Chat Completions + SSE streaming;
- socket guards denying non-loopback runtime egress;
- no real provider key;
- `skip_memory=True`;
- `skip_context_files=True`;
- background review disabled;
- a deliberately enabled temporary plugin registering observer hooks and an unauthorized `amodex_rogue_tool`;
- Tool Search disabled for the initial Amodex worker profile so the model-facing surface is explicit/eager.

## Successful evidence

Workflow: `stage0-hermes`

Successful run:

```text
run_id: 41179436424
job_id: 124473634046
commit: f34b2cc2f7c5110ee960b431839447c54985b8b4
conclusion: success
```

Runner facts:

- GitHub-hosted Ubuntu `26.04.1`;
- runner image `ubuntu-26.04`, version `20261025.179.1`;
- `actions/checkout` pinned to `11d5960a326750d5838078e36cf38b85af677262`;
- Hermes checkout verified exactly at `adecc4024456b387b1518813b90bd60c82b51287`.

The successful probe produced:

```json
{
  "context_injection": true,
  "delegation_exposed": false,
  "external_authority_recovery": true,
  "hard_interrupt": true,
  "hermes_commit": "adecc4024456b387b1518813b90bd60c82b51287",
  "license": "MIT",
  "observer_events": [
    "pre_api_request",
    "post_api_request",
    "pre_tool_call",
    "post_tool_call",
    "pre_api_request",
    "post_api_request"
  ],
  "programmatic_embedding": true,
  "provider_control": "loopback_openai_compatible_sse",
  "provider_file_toolset": [
    "patch",
    "read_file",
    "search_files",
    "write_file"
  ],
  "raw_file_toolset": [
    "patch",
    "read_file",
    "search_files",
    "write_file"
  ],
  "rogue_plugin_leaked": false,
  "rogue_plugin_registered": true,
  "tool_search_policy": "off_for_initial_amodex_worker"
}
```

The runtime log also emitted:

```text
⚡ Interrupted during API call.
```

while the hard-cancel sub-probe still completed successfully, proving that `AIAgent.interrupt(hard_cancel=True, ...)` can terminate the deliberately blocked local API call within the probe bound.

## Proven authority properties

### 1. Programmatic embedding

`AIAgent` was imported and executed directly from Python. The CLI is not the authority boundary.

### 2. Context injection

The pinned commit's supported per-run overlay is `ephemeral_system_prompt`. The Amodex sentinel was observed in the actual request sent to the local provider.

This is suitable for task-specific Context Capsule/system overlays because it does not need to become durable Hermes session authority.

### 3. Final tool whitelist

For the initial worker profile, Tool Search was configured off and the raw scoped catalog equalled the provider-facing surface exactly:

```text
patch
read_file
search_files
write_file
```

No terminal, web, delegation, or rogue plugin tool leaked into the provider request.

### 4. Delegation disabled

`delegate_task` was absent from the raw scoped toolset and from the actual provider-facing tool list.

### 5. Plugin containment

The temporary plugin was genuinely discovered/enabled and successfully registered `amodex_rogue_tool` in its own toolset. The probe then showed that a worker restricted to `enabled_toolsets=["file"]` did not expose that tool to the model.

This is stronger evidence than simply turning plugin discovery off.

### 6. Observer seam

The documented `hermes.observer.v1` hook path emitted all required lifecycle events:

- `pre_api_request`;
- `post_api_request`;
- `pre_tool_call`;
- `post_tool_call`.

These hooks are the preferred integration seam for Amodex worker telemetry instead of scraping console output.

### 7. Provider control

The worker was constructed with provider/base URL/model supplied externally and ran entirely against the Amodex loopback fake provider.

Amodex therefore does not need to let Hermes choose provider routing policy.

### 8. Hard cancellation

A deliberately blocked provider request was interrupted through Hermes while running in another thread. This validates a viable worker-local primitive for lease revocation/cancellation.

Stage 1 must still wrap this with process-level termination deadlines because no Python-level cancellation primitive should be the final safety boundary.

### 9. External durable authority

The probe stored Amodex authority outside `HERMES_HOME`, destroyed the Hermes home/session state, re-read the external snapshot, constructed another worker, and completed a new conversation.

This proves Hermes session persistence is not required to reconstruct Amodex task authority.

It does **not** claim Stage 1 durable PostgreSQL recovery is already implemented.

## Tool Search decision

Current Hermes has progressive tool discovery (`tool_search`, `tool_describe`, `tool_call`) and rechecks scoped toolsets before deferred execution.

For the first Amodex worker implementation, Tool Search remains **off** because:

- the final provider-facing capability surface is easier to audit;
- capability authorization is already an Amodex responsibility;
- P0-02 does not need context-optimization complexity.

Tool Search may be reintroduced later as an optimization only after a dedicated capability-security test proves equivalent authorization under deferred discovery.

## Runtime footprint observation

The official Hermes `activate` path on the successful runner installed or materialized a substantial worker environment, including approximately:

- ffmpeg: `120.7 MiB`;
- Node: `32.5 MiB`;
- Python tool payload: `72.7 MiB`;
- Chromium: `167.3 MiB`;
- CUA driver: `27.5 MiB`;
- 100+ Python runtime packages plus a larger test environment.

This is not a criticism of Hermes; it reflects its broad tool surface. It is evidence that the Control Plane should remain lightweight and that later Runner PoCs must measure cold bootstrap/cache/RAM behavior before selecting a Northflank runner image strategy.

## Failed attempts that improved the boundary

The earlier failures were retained as engineering evidence instead of being hidden:

1. run `41178329638` — invalid temporary plugin fixture lacked `manifest_version: 1`;
2. run `41178613237` — discovered Tool Search/progressive-disclosure assembly (`tool_search/tool_describe/tool_call`), leading to separate raw-catalog and final-surface tests;
3. run `41178814659` — documentation/API drift: pinned constructor uses `ephemeral_system_prompt`, not `system_message`;
4. run `41179053793` — fake provider returned normal JSON while the real conversation path expected streaming SSE;
5. run `41179436424` — corrected SSE fixture; complete boundary probe passed.

No acceptance criterion was weakened to make the run green.

## Frozen integration direction from P0-02

1. Hermes is **not** imported into the Amodex Control Plane process.
2. A dedicated Hermes worker runtime owns its Hermes environment.
3. Amodex passes provider/model/base URL rather than delegating routing authority to Hermes defaults.
4. Amodex passes task context through a bounded per-run system overlay (`ephemeral_system_prompt` for this pinned upstream).
5. Amodex computes the allowed capability/toolset set; Hermes receives that bounded set.
6. delegation remains disabled unless a future TaskPackage explicitly grants it and a later stage implements that authority path.
7. Tool Search remains off initially.
8. plugin/MCP registration does not imply authorization; final toolset filtering remains mandatory.
9. observer hooks bridge worker events into Amodex telemetry.
10. hard interrupt is used as a cooperative/worker-local cancellation primitive, backed later by Runner/process termination.
11. Amodex durable state must remain outside Hermes session state.
12. Hermes upgrades require rerunning this boundary probe against the new exact commit/version.

## Architecture impact

P0-02 validates the Part 1/2 decision to use Hermes as a **worker-runtime substrate** while strengthening the deployment boundary: Control Plane and Hermes workers use separate reproducible environments/processes.

P0-02 is complete and unlocks **P0-03 — real zero-cost provider structured/tool-call capability probing**.
