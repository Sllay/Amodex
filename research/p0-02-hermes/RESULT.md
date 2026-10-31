# P0-02 — Hermes Embedding and Authority Isolation

Status: **IN_PROGRESS**

This PoC evaluates current Hermes Agent as a worker-runtime substrate for Amodex. It does not grant Hermes project governance authority and does not add a production Hermes adapter to `src/amodex/`.

## Upstream under test

Repository: `NousResearch/hermes-agent`

Pinned commit:

```text
adecc4024456b387b1518813b90bd60c82b51287
```

The commit pin is deliberate. A moving `main` is not acceptable evidence for a runtime boundary.

## Why this PoC exists

Amodex only keeps Hermes if the upstream runtime can remain subordinate to Amodex's deterministic authority.

The probe must establish, against the real source checkout:

1. programmatic `AIAgent` construction/invocation without CLI control;
2. explicit Amodex system/context injection;
3. final exposed-tool whitelisting;
4. delegation absent from a non-delegating worker profile;
5. observer-grade provider/tool lifecycle events;
6. interruption of an in-flight run;
7. provider/base URL/model chosen outside Hermes defaults;
8. Amodex authority reconstructable without Hermes session state;
9. an enabled Hermes plugin can register a tool but that tool cannot escape an Amodex-selected toolset whitelist;
10. the pinned upstream license remains MIT.

## Zero-cost test strategy

`probe.py` follows the pattern already used by Hermes upstream provider probes:

- temporary `HOME` / `HERMES_HOME` / working directory;
- exact source checkout;
- loopback `ThreadingHTTPServer` implementing the OpenAI-compatible chat-completions shape;
- explicit socket guard that rejects non-loopback egress during runtime requests;
- no real provider key;
- no paid model call;
- project context and memory disabled;
- a temporary enabled probe plugin that registers observer hooks plus a deliberately unauthorized `amodex_rogue_tool`;
- actual `read_file` tool round through Hermes when the file toolset is permitted.

## Security property under test

The probe intentionally demonstrates both sides of the plugin boundary:

```text
Hermes plugin discovery
        |
        +--> rogue tool really registers
        |
        v
Amodex worker profile: enabled_toolsets=["file"]
        |
        v
final model-facing definitions
        |
        +--> read_file allowed
        +--> delegate_task absent
        +--> terminal/web absent
        +--> amodex_rogue_tool absent
```

A test that merely disables plugin discovery would not prove this property.

## Observability property under test

The probe uses Hermes' documented observer contract and requires all of:

- `pre_api_request`;
- `post_api_request`;
- `pre_tool_call`;
- `post_tool_call`;
- `telemetry_schema_version == "hermes.observer.v1"`.

This avoids treating private print/log behavior as the production observability seam.

## Cancellation property under test

A second local provider request deliberately blocks. The probe calls:

```python
agent.interrupt(
    hard_cancel=True,
    tool_reason="amodex_lease_revoked",
)
```

from another thread and requires the run to end within the bounded probe deadline rather than waiting for the fake provider's full delay.

## Durable-authority property under test

The Amodex authority snapshot is written outside `HERMES_HOME`. After the first worker run, the probe destroys Hermes' temporary home/session state and creates another `AIAgent` from the external snapshot.

Passing this does not mean Stage 1 persistence exists yet. It proves only the P0-02 architectural requirement that Hermes session storage is not necessary to reconstruct Amodex task authority.

## Current evidence

The probe and dedicated CI workflow have been committed. No pass result is claimed until the workflow executes successfully against the pinned Hermes checkout.

Potential outcomes remain:

- **ADOPT** — upstream boundary is sufficient with a thin adapter;
- **ADAPT** — Hermes is usable but requires an explicit isolation/process wrapper or small bounded compatibility layer;
- **REJECT** — the required authority boundary cannot be enforced safely without owning too much upstream behavior.
