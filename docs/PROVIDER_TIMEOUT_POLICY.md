# Provider request timeout policy

Amodex resolves HTTP request budgets in the provider execution registry. The policy is selected for each provider, model, and operation; it does not change routing eligibility, billing admission, retry policy, or task state.

## Default read budgets

| Provider | Chat | Benchmark |
|---|---:|---:|
| NVIDIA | 900 s | 240 s |
| Gemini | 600 s | 180 s |
| Groq | 300 s | 120 s |
| OpenRouter | 600 s | 180 s |
| Other/unlisted combinations | 300 s | 300 s |

The default connection budget is 10 seconds, write budget is 60 seconds, and connection-pool acquisition budget is 10 seconds. Read overrides are clamped to 1–1800 seconds.

## Environment overrides

The most specific configured variable wins:

1. `AMODEX_PROVIDER_TIMEOUT_<PROVIDER>_<MODEL>_<OPERATION>_<COMPONENT>_SECONDS`
2. `AMODEX_PROVIDER_TIMEOUT_<PROVIDER>_<MODEL>_<COMPONENT>_SECONDS`
3. `AMODEX_PROVIDER_TIMEOUT_<PROVIDER>_<OPERATION>_<COMPONENT>_SECONDS`
4. `AMODEX_PROVIDER_TIMEOUT_<PROVIDER>_<COMPONENT>_SECONDS`
5. `AMODEX_PROVIDER_TIMEOUT_<COMPONENT>_SECONDS`

Components are `CONNECT`, `READ`, `WRITE`, and `POOL`. Provider, model, and operation names are uppercased; non-alphanumeric runs become underscores. The canonical model identifier is the **complete provider model ID, including its namespace**. For example, `mistralai/mistral-large-4-1t-a52b` becomes `MISTRALAI_MISTRAL_LARGE_4_1T_A52B`. A model without a namespace uses its complete unqualified ID.

For compatibility, the historical `MISTRAL_LARGE_4_1T_A52B` alias is accepted **only** for NVIDIA's exact `mistralai/mistral-large-4-1t-a52b` ID. This is an explicit provider/model mapping, not a generic basename fallback. An override for that alias does not apply to another publisher, a version suffix, a `:free` variant or another provider. Other models require their complete ID.

At each model-specific precedence level, a valid canonical override wins over a valid alias. Specificity takes priority: a model+operation alias still wins over a canonical model override without an operation. Invalid candidates fall through to the next candidate. Non-finite numbers, non-positive numbers and non-numeric values are ignored; finite positive values for **all four components** are clamped to 1–1800 seconds. No retry or routing policy changes follow from resolution.

Normalization is a legacy environment-variable convention, not a new model identity: punctuation and case can collapse (for example `author/model-a` and `author/model_a`). Do not register such distinct IDs under the same normalized override key or add aliases by stripping arbitrary namespaces. They cannot be disambiguated by that environment-variable spelling. Use a provider/global budget until a distinct, explicitly reviewed mapping is defined; the resolver does not guess a publisher from a basename. The scoped compatibility mapping above is limited to the one documented identity, and tests cover namespace/basename collisions and alias precedence.

Canonical example for a longer NVIDIA Mistral Large 4 chat response:

```env
AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRALAI_MISTRAL_LARGE_4_1T_A52B_CHAT_READ_SECONDS=1200
```

Previously documented spelling, retained as the explicit NVIDIA compatibility alias:

```env
AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRAL_LARGE_4_1T_A52B_CHAT_READ_SECONDS=1200
```

Example for every NVIDIA provider request:

```env
AMODEX_PROVIDER_TIMEOUT_NVIDIA_READ_SECONDS=1200
```

## Important execution boundary

These values are per-upstream-request transport budgets, not deadlines for an entire agent task. The current adapter transport waits for a complete response and does not yet provide model-token streaming or periodic provider heartbeats. Multi-step work lasting minutes or hours still requires durable run orchestration, checkpoints, lease renewal, and safe resume.

A timeout remains an ambiguous provider outcome where the request may already have been processed. Do not automatically replay it unless the provider offers a verified idempotency guarantee.
