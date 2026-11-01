# Post-baseline Model Intelligence V1

This document defines **Step 4** of the web-first Amodex product program.

Step 4 turns the Stage 5 declared model catalog and deterministic capability router into a measured, durable model-intelligence layer. A model declaration, API key, successful smoke request, or provider marketing claim is **not** enough to make a route eligible. Eligibility comes from benchmark evidence plus current zero-cost/quota state.

The existing Control Plane remains the authority owner. Environment remains the only execution authority for project work. Git promotion remains governed by the existing exact-SHA PromotionGate.

## Scope

Step 4 adds:

- explicit capability benchmarks for declared free model routes;
- durable benchmark runs and case-level evidence;
- measured lifecycle promotion and demotion;
- capability-specific leaderboards;
- automatic chat model routing;
- quality, quota, latency and zero-cost selection;
- one bounded fallback route;
- `UNKNOWN_OUTCOME` handling without blind retry;
- trusted server-side Amodex chat replies;
- route-attempt audit records;
- automatic demotion after repeated non-ambiguous failures;
- mobile-friendly model/benchmark status surfaces.

Step 4 does **not** grant model workers repository authority, runner authority, promotion authority, approval authority, or permission to spend money.

## Migration

Apply after the existing post-baseline migrations:

```text
migrations/postbaseline/0006_model_intelligence.sql
```

The migration adds durable tables for:

- declared model routes and lifecycle;
- benchmark runs and benchmark cases;
- lifecycle events;
- route decisions;
- per-route attempts.

Provider credentials are not stored in these tables.

## Lifecycle

Declared routes enter the durable registry with their Stage 5 lifecycle, normally:

```text
BENCHMARK_PENDING
```

A benchmark may promote a route only through the measured policy:

```text
sample_count >= 5
aggregate_score >= 0.86
pass_rate >= 0.80
minimum_case_score >= 0.70
    -> ELIGIBLE

sample_count >= 3
aggregate_score >= 0.75
pass_rate >= 0.67
    -> ELIGIBLE_LIMITED

otherwise
    -> BENCHMARK_PENDING
```

A successful connectivity smoke is not a benchmark and cannot promote a model.

Sensitive profiles such as implementation, correction, review, integration, Godot and visual/runtime validation require stronger evidence. `ELIGIBLE_LIMITED` is not sufficient for those profiles.

## Benchmark suite

The first benchmark suite is versioned as:

```text
step4-live-v1
```

Current machine-verifiable text cases can measure declared support for:

- text;
- structured output;
- planning;
- research following;
- coding;
- correction;
- review.

The current generic `ProviderClient.generate()` interface does **not** truthfully prove:

- native tool-calling transport;
- real vision input;
- long-context behavior at advertised limits.

Therefore Step 4 deliberately does **not** assign fake passing scores for those properties. Vision routing requires an actual non-null `vision_success_rate` from compatible evidence before the visual/runtime profile can become routable.

Benchmark prompts never execute generated code. They use bounded exact-output checks.

## Running a benchmark

Benchmarks are explicit owner actions. Opening the model dashboard does not make external model calls.

API:

```text
POST /v1/model-intelligence/benchmarks/run
```

Payload:

```json
{
  "route_id": "groq:openai/gpt-oss-20b:groq-free-primary",
  "force": false
}
```

A successful benchmark consumes the configured provider's free quota. A short cooldown prevents accidental repeated benchmark storms. `force=true` is still subject to the zero-cost execution boundary.

A route whose model cost or credential pool is `paid` or `unknown` is denied before execution.

## Secrets and spending

Stage 4 reuses `ProviderExecutionRegistry`.

Credential values are resolved only from environment-backed secret references at the final provider boundary. They are never persisted into benchmark evidence, routing decisions, chat messages or leaderboards.

Required principle:

```text
API key present != permission to spend
```

Both the route and its credential pool must be explicitly classified `free`.

There is no automatic paid fallback.

## Leaderboard

Capability leaderboards are derived from:

1. durable declared route lifecycle;
2. recent benchmark-derived `ModelMeasurement`;
3. recent provider availability/quota evidence;
4. existing `CapabilityRouter` hard gates;
5. profile-specific quality floors;
6. a small efficiency tie-break based on measured useful progress per 1k output tokens.

Hard gates always run before scoring. High quality cannot compensate for:

- paid/unknown billing;
- missing capability;
- stale/absent measurement;
- stale/absent quota state;
- unavailable provider;
- insufficient context;
- insufficient lifecycle state.

Evidence freshness defaults:

```text
measurement <= 7 days
availability <= 6 hours
```

## Automatic chat inference

After at least one route becomes measurable and routable, the Workspace may request a trusted Amodex response:

```text
POST /v1/chats/{chat_id}/respond
```

The browser still cannot create `AMODEX` messages directly. The trusted server:

1. validates that the source message exists and belongs to the chat;
2. requires the source role to be `OWNER`;
3. chooses or infers a bounded worker profile;
4. builds prompt context from recent chat plus **approved** Project Memory only;
5. selects the measured zero-cost route;
6. records the routing decision before provider execution;
7. performs at most one primary attempt plus one fallback attempt;
8. persists the trusted Amodex message only after a successful provider result.

Conversation does not itself execute code. To perform project work, the user still hands work to an authorized Environment/Run.

## Fallback and unknown outcomes

Fallback is bounded:

```text
maximum route attempts per chat response = 2
```

The fallback must independently pass the same capability, lifecycle, billing, quota and evidence gates.

If the provider request has an ambiguous outcome, for example a timeout after the request may have reached the provider:

```text
decision -> UNKNOWN_OUTCOME
no blind retry
no automatic fallback
```

The stale-decision reconciliation endpoint can later mark abandoned local `STARTED` records as `UNKNOWN_OUTCOME`; it does not re-execute the provider request.

## Idempotency

A route decision is uniquely bound to the source OWNER message. Repeating the response request for a message that already succeeded returns the existing decision rather than generating a second Amodex reply.

## Automatic demotion

Persistent non-ambiguous route failures are observed independently from benchmark quality.

After three recent failed route attempts inside the bounded failure window, the route is demoted one step:

```text
ELIGIBLE
  -> ELIGIBLE_LIMITED
  -> BENCHMARK_PENDING
```

The lifecycle transition is durably audited with actor `STEP4_FAILURE_POLICY`.

A route does not auto-promote again merely because the provider becomes reachable; it must satisfy measured promotion policy again.

## API

Core endpoints:

```text
GET  /v1/model-intelligence/status
GET  /v1/model-intelligence/routes
GET  /v1/model-intelligence/leaderboard
GET  /v1/model-intelligence/benchmarks
POST /v1/model-intelligence/benchmarks/run
POST /v1/model-intelligence/reconcile

POST /v1/chats/{chat_id}/respond
GET  /v1/chats/{chat_id}/model-decisions
```

There is intentionally no public `promote model` or `set lifecycle` endpoint.

## Product UI

The Workspace shows a Step 4 inference badge and can issue a routed Amodex reply after the user's OWNER message has been stored.

The model dashboard is available at:

```text
/models
```

It exposes:

- route lifecycle;
- latest benchmark evidence;
- explicit benchmark controls;
- profile leaderboard;
- reconciliation control.

Running a benchmark asks for confirmation because it makes real provider requests and consumes free quota.

## Deployment sequence

For a real deployment:

1. deploy the Step 4 code;
2. apply `0006_model_intelligence.sql`;
3. configure provider secret environment variables;
4. explicitly classify only genuinely free credential pools with `AMODEX_*_BILLING_CLASS=free`;
5. open `/models`;
6. run an explicit benchmark for each intended route;
7. verify lifecycle and leaderboard;
8. only then rely on automatic chat inference.

If no measured eligible route exists, chat inference fails closed with `NO_MEASURED_ROUTE` rather than choosing an unmeasured or paid model.

## CI boundary

The deterministic Step 4 CI uses injected fake providers to prove:

- benchmark scoring;
- lifecycle promotion;
- lifecycle demotion;
- leaderboard routing;
- one-fallback limit;
- idempotency;
- ambiguous-outcome handling;
- zero-cost denial;
- PostgreSQL persistence;
- compatibility with Steps 1-3, Stage 5 routing, Environment and Stage 6.

That CI is a contract/integration test. It is **not** a claim that any real provider is currently reachable, free, or high quality.

Real quality evidence is created only by an explicit live benchmark in the deployed Amodex instance.

## Authority boundary

Step 4 owns model evidence and model selection. It does not own project mutation authority.

```text
Owner
  -> Workspace chat
       -> Step 4 measured model router
            -> trusted Amodex response

Owner-approved project work
  -> Environment
       -> TaskPackage / worker
            -> validation
                 -> exact-SHA Git PromotionGate
```

Model Intelligence cannot bypass Environment ownership, path leases, budget policy, approvals, validation evidence or Git promotion rules.
