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

Components are `CONNECT`, `READ`, `WRITE`, and `POOL`. Provider, model, and operation names are uppercased; non-alphanumeric characters become underscores.

Example for a longer NVIDIA Mistral Large 4 chat response:

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
