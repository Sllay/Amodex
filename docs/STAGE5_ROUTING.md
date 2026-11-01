# Stage 5 — Specialized workers and measured capability routing

Status: **Stage 5 implementation contract**

Stage 5 improves quality/efficiency without introducing an uncontrolled swarm.
The deterministic Orchestrator remains authoritative for DAG state, concurrency,
writer ownership, correction scheduling, validation ordering, and promotion.
Worker profiles only narrow model capability requirements and context disclosure.

## Specialized profiles

Initial profiles are implemented in `amodex.providers.capabilities`:

- repository analyst;
- research worker;
- implementation worker;
- correction/debug worker;
- reviewer;
- integration/migration worker;
- Godot specialist;
- visual/runtime validation worker.

A profile is **not** a provider/model identity. It does not grant standing
repository authority. In particular, research/reviewer/visual-validation profiles
are read-only and cannot execute a task with write scope.

Reviewer context is intentionally limited to contract + diff + acceptance/evidence
channels. Implementation transcript/private reasoning is not a review input.

## Routing order

`CapabilityRouter` evaluates hard gates before score:

1. profile exists;
2. route enabled;
3. provider is not denied by task policy;
4. model lifecycle is `ELIGIBLE_LIMITED` or `ELIGIBLE`;
5. route is classified free;
6. required model capabilities are present;
7. context requirement fits;
8. measured observation exists with enough samples;
9. credential pool is explicitly classified free;
10. provider health permits execution;
11. free quota is currently available.

Only then are eligible models scored using role quality, structured/tool
reliability, planning/schema compliance, latency, current free capacity, and
context fit. A high score can never compensate for a failed hard gate.

Fallback excludes the failed route and repeats the full hard-gate process. Vision,
tool-calling, context, and billing semantics therefore cannot silently disappear
on fallback.

## Measurement provenance

Provider/model observations and provider availability are durable in PostgreSQL via:

- `migrations/stage5/0001_routing_measurements.sql`;
- `PostgresRoutingRepository`.

Historical measurements include sample count, structured/tool reliability, coding
pass rate, correction success, planning/schema compliance, research success,
review detection, vision success when applicable, latency, timestamp, and source.

Availability snapshots include credential pool ID, billing class, health, quota
availability, free-capacity ratio, timestamp, and reason. No raw API key or secret
value is stored in these tables.

## Provider adapters

Stage 5 adds real adapters for the user's configured provider set while retaining
Gemini from Stage 1.

### Groq

- API compatibility: OpenAI-compatible.
- Base URL: `https://api.groq.com/openai/v1`.
- Current conservative catalog candidate: `openai/gpt-oss-20b`.
- Official docs observed 2026-11-01 describe tool use, JSON/structured output and
  a 131,072-token context for the candidate.
- Credential reference: `GROQ_API_KEY`.
- Billing policy variable: `AMODEX_GROQ_BILLING_CLASS`.

References:

- <https://console.groq.com/docs/openai>
- <https://console.groq.com/docs/tool-use/overview>
- <https://console.groq.com/docs/rate-limits>

### OpenRouter

- API compatibility: OpenAI-compatible.
- Base URL: `https://openrouter.ai/api/v1`.
- Conservative default: `openrouter/free`.
- The free router is deliberately treated as text/research/planning only until a
  concrete backend/model is benchmarked; dynamic routing is not allowed to invent
  hard tool/vision guarantees.
- Credential reference: `OPENROUTER_API_KEY`.
- Billing policy variable: `AMODEX_OPENROUTER_BILLING_CLASS`.

References:

- <https://openrouter.ai/developers>
- <https://openrouter.ai/collections/free-models>

### NVIDIA NIM / Mistral

- API compatibility: OpenAI chat completions.
- Base URL: `https://integrate.api.nvidia.com/v1`.
- Current catalog candidate: `mistralai/mistral-large-4-1t-a52b`.
- NVIDIA's current model documentation describes text+image input, reasoning,
  coding, structured outputs, tool calling and up to a 1M-token context; the Build
  catalog labels the hosted endpoint as a free endpoint at the 2026-11-01 snapshot.
- Credential reference: `NVIDIA_API_KEY`.
- Billing policy variable: `AMODEX_NVIDIA_BILLING_CLASS`.

References:

- <https://docs.api.nvidia.com/nim/reference/llm-apis>
- <https://docs.api.nvidia.com/nim/reference/mistralai-mistral-large-4-1t-a52b>
- <https://build.nvidia.com/models>

## Catalog lifecycle

Provider-declared capability is not sufficient for production routing. All Stage 5
catalog entries start as `BENCHMARK_PENDING`, including Mistral Large 4. They must
receive observed measurements and be deliberately promoted to `ELIGIBLE_LIMITED`
or `ELIGIBLE` before `CapabilityRouter` can select them.

This prevents model/provider marketing, a newly appearing catalog entry, or the
presence of an API key from changing production behavior automatically.

## Secret-safe execution boundary

`ProviderExecutionRegistry` stores only credential-pool metadata and the name of
the environment variable containing the key. It resolves the actual secret only
when constructing the provider client after a free route has already been
admitted. A free model with a paid/unknown credential pool is denied.

Conventional environment variables:

```text
GEMINI_API_KEY
GROQ_API_KEY
OPENROUTER_API_KEY
NVIDIA_API_KEY

AMODEX_GEMINI_BILLING_CLASS=free|paid|unknown
AMODEX_GROQ_BILLING_CLASS=free|paid|unknown
AMODEX_OPENROUTER_BILLING_CLASS=free|paid|unknown
AMODEX_NVIDIA_BILLING_CLASS=free|paid|unknown
```

Optional model overrides used by live probes:

```text
AMODEX_GROQ_MODEL
AMODEX_OPENROUTER_MODEL
AMODEX_NVIDIA_MODEL
```

Default is `unknown`, not `free`.

## Stage 5 vertical slice

The deterministic benchmark executes:

```text
research profile
      |
      v
implementation profile
      |
      +-- deliberate deterministic failure
      v
correction profile
      |
      v
review profile
```

The existing `DagOrchestrator` remains the sole state/ownership authority.
Specialization is compared with a Stage-4-style single generalist profile using:

- role-quality measurement;
- request count;
- modeled wall time from observed latency fields;
- quota request count;
- context characters disclosed.

The benchmark must improve at least one dimension without increasing authority,
overlapping writers, request count, or quota consumption.

## Live probes

`test_stage5_live_providers.py` can exercise Groq, OpenRouter, and NVIDIA using real
keys. A probe runs only when both the secret exists and its billing-class variable
is explicitly `free`. Missing keys or `unknown`/`paid` classifications skip the
network call rather than silently spending.

Live probe success does not auto-promote a model to production eligibility; it is
measurement evidence to feed the governed model-profile lifecycle.
