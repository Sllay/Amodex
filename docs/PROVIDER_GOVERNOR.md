# Amodex Provider Governor

Status: **Normative architecture contract — Part 2/3**

This document defines how Amodex selects, schedules, throttles, retries, pauses, and falls back across remote AI providers while preserving the project's zero-cost policy and avoiding request storms.

The Provider Governor is a deterministic control-plane service. It is not a prompt convention and it is not delegated to Hermes or to a worker model.

---

## 1. Primary goals

The Governor must guarantee:

1. no paid inference is used unless owner policy explicitly permits it;
2. free-tier capacity is treated as finite shared infrastructure;
3. providers are never spammed with uncontrolled retries;
4. workers cannot bypass request/concurrency budgets;
5. fallback only occurs when the replacement can satisfy the task contract;
6. provider-specific errors are normalized into stable internal categories;
7. task progress survives temporary provider unavailability;
8. cost, latency, quota, and capability decisions are observable;
9. provider API keys are never exposed to worker prompts or repository logs;
10. routing can evolve as models/providers change without rewriting task logic.

---

# 2. Zero-cost invariant

Default policy:

```yaml
cost_policy:
  id: zero_cost_v1
  paid_inference_allowed: false
  paid_compute_allowed: false
  auto_upgrade_to_paid: false
  auto_purchase_credits: false
  allow_credit_card_required_free_tier: owner_policy
  on_free_capacity_exhausted: WAIT
  owner_approval_required_for_paid: true
```

The system must never infer permission to spend because:

- an API key is present;
- the provider has a paid balance;
- a free quota is exhausted;
- a fallback provider would be faster;
- a model claims the task is important.

Paid enablement is a durable owner approval with explicit scope and expiry.

Recommended approval scopes:

- one request;
- one attempt;
- one run;
- provider/model bounded;
- amount/cost ceiling bounded;
- time bounded.

Global permanent paid permission is not the default.

---

# 3. Provider abstractions

## 3.1 `ProviderAdapter`

Every provider is normalized behind an adapter capable of:

```text
capabilities()
models()
prepare_request(...)
execute(...)
parse_response(...)
classify_error(...)
extract_usage(...)
extract_retry_after(...)
health_probe(...)
```

Adapters may reuse Hermes provider plumbing where compatible, but Governor policy remains outside Hermes.

## 3.2 `CredentialPool`

Credentials are represented by references, not raw keys.

```yaml
credential_pool:
  id: google-free-primary
  provider: google
  billing_class: free
  secret_ref: secret://providers/google/free-primary
  owner_scope: personal
  status: ACTIVE
  quota_profile: discovered_or_configured
```

Rules:

- secret material is resolved only in the provider execution boundary;
- prompts never receive API keys;
- logs store credential pool ID, not secret values;
- multiple credentials may exist only when permitted by provider terms and owner policy;
- rotating keys to evade provider limits is forbidden.

---

# 4. Quota model

Providers differ. The Governor therefore models quota as a set of dimensions, any of which may be unknown.

Supported dimensions include:

- requests per minute (`RPM`);
- requests per day (`RPD`);
- input tokens per minute (`ITPM`);
- output tokens per minute (`OTPM`);
- total tokens per minute (`TPM`);
- tokens per day;
- concurrent requests;
- per-model concurrency;
- per-account concurrency;
- burst capacity;
- image/vision request limits;
- tool-call restrictions;
- context-window limits;
- provider-specific free-credit balance;
- daily/monthly monetary free allowance where applicable.

Unknown quota is not treated as infinite quota.

## 4.1 Quota source precedence

Quota values may come from:

1. provider response headers/API metadata;
2. provider account/quota endpoint;
3. owner configuration;
4. documented static defaults captured with source/date;
5. conservative inferred behavior from recent throttling.

When sources disagree, the more restrictive current value wins until reconciled.

---

# 5. Admission control

Before any provider request, the worker asks the Governor for a `ProviderLease`.

Admission checks:

```text
cost policy
   +
provider health
   +
credential health
   +
model capability requirements
   +
context/token limits
   +
rate buckets
   +
concurrency slots
   +
run/attempt request budget
   +
fairness
   =
ALLOW / WAIT / DENY
```

No valid lease -> no governed provider call.

---

# 6. Rate limiting strategy

A single mechanism is not enough.

Amodex combines:

- token buckets for burst-aware request/token quotas;
- semaphores for concurrency;
- sliding/rolling accounting for provider windows;
- cooldown timers from `Retry-After` or classified errors;
- circuit breakers for repeated systemic failure;
- task-level request budgets;
- global fairness scheduling.

## 6.1 Token bucket

Conceptual state per quota dimension:

```yaml
bucket:
  capacity: 30
  refill_amount: 30
  refill_period_seconds: 60
  available: 11.4
  last_refill_at: ...
```

The exact persistence strategy can optimize later, but authoritative admission must survive process restart closely enough to prevent a restart from intentionally resetting limits.

## 6.2 Concurrency semaphore

Example:

```yaml
provider_concurrency:
  provider: groq
  credential_pool: free-primary
  max_active: 3
  active: 2
```

Concurrency may be more restrictive than RPM and must be enforced independently.

---

# 7. Scheduling and fairness

A long task must not monopolize every free request slot.

The queue should support weighted fair scheduling across:

- projects;
- runs;
- task priorities;
- interactive vs background work;
- provider/model pools.

Initial policy recommendation:

```text
owner-interactive control requests   highest normal priority
promotion-blocking correction        high
active implementation                normal
research/background analysis         lower
benchmark/re-evaluation               lowest
```

Priority does not bypass zero-cost or quota rules.

Prevent starvation by aging waiting requests over time.

---

# 8. Model eligibility

The router does not simply choose the model with the highest score.

A model is **eligible** only if all hard requirements pass:

- billing class permitted;
- provider/credential healthy;
- required modality supported;
- required structured-output support available;
- required tool-calling semantics available;
- context requirement fits;
- task security policy permits provider;
- data residency/privacy rules permit provider where applicable;
- current quota allows admission or queued waiting;
- owner/project provider denylist does not exclude it.

Then the router scores eligible candidates.

---

# 9. Capability score

Initial scoring inputs may include:

- coding quality;
- debugging quality;
- planning;
- research;
- vision;
- tool use;
- structured-output reliability;
- long-context behavior;
- latency;
- recent malformed-tool-call rate;
- recent task success rate;
- free capacity pressure;
- context compression risk.

Example conceptual score:

```text
score =
  capability_fit * 0.45
+ reliability    * 0.20
+ tool_quality   * 0.15
+ latency        * 0.05
+ free_capacity  * 0.10
+ context_fit    * 0.05
```

Weights are configuration/benchmark outputs, not permanent constants.

Hard capability mismatches cannot be compensated by score.

---

# 10. Dynamic capability profiles

Model profiles have provenance and confidence.

Sources:

- provider-declared features;
- Amodex benchmark suite;
- project-specific outcomes;
- observed structured-output/tool-call reliability;
- recent failures.

A model update/new version creates a new profile observation rather than silently inheriting all scores from its predecessor.

The Governor stores historical measurements so routing changes are explainable.

---

# 11. Fallback policy

Fallback occurs only after error classification.

Bad behavior:

```text
request failed -> immediately ask another random model
```

Correct behavior:

```text
failure
  -> classify
  -> reconcile request outcome
  -> decide retryability
  -> honor cooldown
  -> verify fallback capability equivalence
  -> acquire new provider lease
  -> perform fallback
```

## 11.1 Fallback equivalence

A fallback must preserve required semantics.

Examples:

- vision task cannot fall back to text-only model;
- task requiring native tool calling cannot move to a model for which the current adapter only supports plain text unless the worker runtime explicitly supports a compatible translation path;
- 200k-token context task cannot move to a 32k model without recompiling/reducing context and recording that strategy change;
- strict structured output requires proven structured-output capability or a validated schema-constrained adapter.

Fallback is a new assignment/attempt decision when semantics materially change.

---

# 12. Provider error taxonomy

HTTP status is evidence, not the full error class. Provider response body/headers and request stage also matter.

Canonical classes:

- `REQUEST_INVALID`;
- `AUTH_INVALID`;
- `AUTH_FORBIDDEN`;
- `MODEL_NOT_FOUND`;
- `CONTEXT_TOO_LARGE`;
- `CONTENT_REJECTED`;
- `QUOTA_EXHAUSTED_WINDOW`;
- `RATE_LIMITED`;
- `CONCURRENCY_LIMITED`;
- `PAYMENT_REQUIRED_OR_BALANCE`;
- `REQUEST_TIMEOUT`;
- `PROVIDER_TIMEOUT`;
- `PROVIDER_OVERLOADED`;
- `PROVIDER_INTERNAL`;
- `NETWORK_TRANSIENT`;
- `MALFORMED_PROVIDER_RESPONSE`;
- `TOOL_PROTOCOL_INCOMPATIBLE`;
- `UNKNOWN_PROVIDER_ERROR`.

## 12.1 HTTP normalization guidance

Typical interpretation, always overridden by provider-specific adapters when the provider documents different semantics:

| HTTP | Default treatment |
|---|---|
| 400 | invalid request; do not blind retry |
| 401 | credential invalid/expired; disable credential until refreshed |
| 402 | payment/balance condition; zero-cost mode denies paid recovery |
| 403 | forbidden/policy/quota depending provider body; classify specifically |
| 404 | model/endpoint not found; refresh model catalog/config |
| 408 | request timeout; outcome usually safe to retry only if request is inference-only and adapter confirms |
| 409 | conflict; provider-specific, inspect response |
| 422 | semantic validation failure; fix request |
| 424 | dependency/upstream failure; usually transient if provider documents so |
| 429 | rate/quota/concurrency; honor Retry-After and provider metadata |
| 500 | provider internal; backoff |
| 502 | gateway/upstream; backoff |
| 503 | overloaded/unavailable; backoff/circuit breaker |
| 504 | gateway timeout; backoff and classify outcome |

Do not turn all 4xx into permanent failures or all 5xx into immediate retries.

---

# 13. Retry policy

Retry belongs to the Governor, not scattered provider-client loops.

## 13.1 Retryable classes

Usually retryable after policy:

- `RATE_LIMITED`;
- `CONCURRENCY_LIMITED`;
- `PROVIDER_OVERLOADED`;
- `PROVIDER_INTERNAL`;
- `NETWORK_TRANSIENT`;
- selected timeout classes.

Usually non-retryable without changing request/config:

- `REQUEST_INVALID`;
- `AUTH_FORBIDDEN`;
- `MODEL_NOT_FOUND`;
- `CONTEXT_TOO_LARGE`;
- `CONTENT_REJECTED`;
- `TOOL_PROTOCOL_INCOMPATIBLE`.

`AUTH_INVALID` may become retryable only after credential refresh.

## 13.2 Backoff

Recommended baseline:

```text
delay = min(cap, base * 2^attempt) + random_jitter
```

`Retry-After`/provider reset metadata takes precedence when credible.

Never busy-loop.

Initial cap/attempt counts are configurable by error class.

## 13.3 Retry budget

A single logical model turn has a bounded transport retry budget.

A task attempt has a separate provider-request budget.

A run has a broader request budget.

This prevents nested layers from each retrying independently and multiplying traffic.

---

# 14. Circuit breaker

State per relevant failure domain, such as provider+region+credential pool or provider+model:

```text
CLOSED
  |
  | threshold systemic failures
  v
OPEN
  |
  | cooldown
  v
HALF_OPEN
  |
  | limited probe success
  v
CLOSED
```

A failed half-open probe returns to `OPEN`.

## 14.1 What counts toward breaker

Count systemic failures:

- repeated 5xx;
- network connection failures;
- provider overload;
- repeated malformed responses from otherwise valid requests.

Do not open provider circuit because the worker sent invalid JSON.

---

# 15. Quota exhaustion behavior

When every eligible free model is temporarily unavailable:

```text
Task -> WAITING_PROVIDER
```

The scheduler records the earliest meaningful recheck time.

It does **not**:

- fail the whole run immediately;
- spin retries;
- silently switch to paid;
- clone credentials;
- ask workers to reduce quality without Director approval.

The Termux/web client can show:

```text
Waiting for free provider capacity
Next eligible check: 14:32
Reason: Google daily quota + Groq cooldown
```

---

# 16. Cost accounting

Even in zero-cost mode, Amodex tracks cost metadata.

Why:

- detect accidental billed calls;
- compare providers;
- prepare future explicit paid mode;
- audit free-credit consumption;
- identify unexpectedly expensive task patterns.

Record when available:

- reported usage;
- provider billing class;
- currency;
- amount;
- free credits consumed;
- estimation confidence.

Unknown is stored as unknown, never converted to zero.

Zero cost means policy forbids chargeable execution, not that every usage record magically reports `$0`.

---

# 17. Request deduplication

Transport-level duplicate inference should be avoided when the same logical request is already in flight.

Use a request identity derived from:

- attempt;
- conversation turn sequence;
- prompt/context digest;
- tool-state digest;
- model assignment;
- request purpose.

If a duplicate caller appears while the original is active, it waits for the original result rather than issuing another provider request.

Do not reuse an old model response after repository/context state changed merely because user text is identical.

---

# 18. Response cache

Caching may be useful for deterministic supporting queries, but agent reasoning responses are unsafe to cache broadly.

Safe candidates:

- model metadata;
- provider capability discovery;
- embeddings for unchanged content where provider terms permit;
- deterministic auxiliary classification under explicit cache key.

Unsafe default candidates:

- implementation turn responses;
- architecture decisions;
- tool-call plans tied to mutable repo state.

Provider-native prompt caching may be used if supported and if it does not introduce paid-only behavior contrary to policy.

---

# 19. Structured-output reliability

A provider/model that claims JSON support is not automatically treated as reliable.

Track:

- schema-valid response rate;
- truncation rate;
- tool argument validation rate;
- refusal/mismatch rate;
- average repair attempts.

The worker runtime may perform one bounded repair step for malformed structured output when policy permits. Repeated repair failure lowers model profile reliability and may trigger fallback.

---

# 20. Tool-call compatibility

Adapters normalize:

- tool name;
- argument schema;
- call ID;
- parallel calls;
- tool result correlation;
- continuation semantics;
- streaming behavior.

If a provider's API cannot preserve semantics required by Hermes/Amodex for a task, that provider is ineligible for that task.

Do not hide semantic incompatibility behind string parsing and call it equivalent tool support.

---

# 21. Long-context strategy

Before routing, estimate:

- system/constitution tokens;
- context capsule tokens;
- current conversation state;
- tool schemas;
- reserved output;
- compression safety margin.

If request exceeds model context:

1. try Context Engine reduction according to authority rules;
2. use semantic symbol retrieval instead of whole files;
3. summarize non-authoritative history with provenance;
4. select larger-context eligible free model;
5. split task only if Director accepts semantic boundary.

Never silently truncate the highest-authority instructions or acceptance criteria.

---

# 22. Privacy and provider policy

Project/provider policy may classify data:

- `PUBLIC`;
- `PROJECT_CONFIDENTIAL`;
- `SECRET`;
- `NEVER_SEND_TO_MODEL`.

Raw secrets are always `NEVER_SEND_TO_MODEL`.

A provider may be eligible for public research but ineligible for private repository code under a future project policy. Routing must support this distinction even if initial personal use permits all configured providers.

---

# 23. Provider health model

Health is multi-dimensional:

```yaml
health:
  transport: HEALTHY
  auth: HEALTHY
  quota: LIMITED
  model_catalog: HEALTHY
  latency_p95_ms: 840
  malformed_rate_15m: 0.01
  breaker: CLOSED
```

A provider can be operational but currently quota-limited.

Health status:

- `HEALTHY`;
- `DEGRADED`;
- `LIMITED`;
- `UNAVAILABLE`;
- `DISABLED_POLICY`.

---

# 24. Provider catalog refresh

Model names and provider features change.

The Governor supports refresh from provider metadata when available, but never auto-enables a new model for sensitive roles solely because it appears in a catalog.

New model lifecycle:

```text
DISCOVERED
 -> PROFILED_DECLARED
 -> BENCHMARK_PENDING
 -> ELIGIBLE_LIMITED
 -> ELIGIBLE
```

High-authority roles such as Technical Director may require stronger benchmark confidence than low-risk research tasks.

---

# 25. Hermes boundary

Hermes currently provides broad provider resolution and agent-loop capabilities. Amodex may reuse those mechanics, but the following remain external Governor responsibilities:

- zero-cost authorization;
- free/paid credential classification;
- cross-worker fairness;
- durable quota accounting;
- task/run budgets;
- circuit-breaker state across worker restarts;
- provider leases;
- model capability scoring tied to Amodex benchmarks;
- wait-state transitions;
- owner paid approval.

A Hermes internal fallback must be disabled or subordinated when it could bypass Governor routing.

---

# 26. Observability

Expose per request without exposing secrets:

- task/attempt;
- provider/model;
- selection reason;
- queue wait;
- lease acquisition;
- retry count;
- normalized error class;
- HTTP status where safe;
- tokens/usage where reported;
- free/paid class;
- latency;
- fallback decision;
- breaker state change.

The user should be able to answer:

> Why is this task waiting?

and

> Why did Amodex choose this model?

without reading hidden chain-of-thought.

---

# 27. Fault-injection tests

The implementation is not complete until tests prove:

1. 429 with `Retry-After` queues without request storm;
2. repeated 503 opens breaker;
3. half-open permits only bounded probes;
4. one task cannot consume all concurrency forever;
5. restart does not reset durable cooldown enough to spam provider;
6. zero-cost policy rejects a paid-only model despite being highest scored;
7. presence of a paid account balance does not authorize spending;
8. all free providers unavailable -> `WAITING_PROVIDER`;
9. malformed structured output is bounded and does not loop indefinitely;
10. context-too-large triggers strategy change, not identical retry;
11. 401 disables credential until refresh/reconfiguration;
12. 404 model invalidation refreshes catalog/config path;
13. fallback rejects modality/tool incompatibility;
14. duplicate logical in-flight request is coalesced;
15. reported nonzero billed cost in zero-cost mode creates an incident event;
16. provider timeout does not cause two concurrent duplicate turns;
17. owner paid approval expiry immediately restores fail-closed behavior.

---

# 28. Initial provider strategy

Initial target adapters/pools may include:

- Google/Gemini;
- Groq;
- NVIDIA NIM/API endpoints;
- OpenRouter free-eligible models;
- additional OpenAI-compatible endpoints that pass compatibility tests.

This is not a promise that any provider permanently offers a specific free quota. Availability is runtime policy/configuration.

OpenAI API is optional and is not assumed to be included in a ChatGPT subscription.

---

# 29. Frozen Part 2 decisions

1. Provider routing is governed centrally, not independently by workers.
2. Every governed provider call acquires a ProviderLease.
3. Zero-cost mode fails closed and never silently upgrades to paid.
4. Quota dimensions are explicit and unknown does not mean infinite.
5. Rate limiting combines buckets, semaphores, cooldowns, and breakers.
6. Retry happens in one authority layer with bounded budgets.
7. 429/5xx do not trigger busy loops.
8. Fallback requires capability equivalence and fresh Governor admission.
9. Provider/model capability profiles are dynamic and evidence-backed.
10. Provider errors normalize into stable internal categories.
11. Cost metadata is tracked even when policy intends zero spend.
12. Credential secrets never enter worker prompt/context.
13. Queueing is a normal state when free capacity is unavailable.
14. Hermes provider features may be reused but cannot override Amodex Governor policy.
15. Provider selection and waiting reasons are observable without exposing chain-of-thought.
