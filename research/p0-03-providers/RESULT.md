# P0-03 — Real Zero-Cost Provider Capability Probe

Status: **BLOCKED_EXTERNAL_CREDENTIAL**

P0-03 is implemented and the branch-safe workflow has executed, but the provider capability cannot honestly be declared proven until Amodex makes at least one real external Free Tier request using a real credential. No API key is stored in source, workflow logs, or result files.

## Primary provider candidate

Provider: Google Gemini Developer API

Fixed probe model:

```text
gemini-3.5-flash-lite
```

Why this model is pinned for the initial probe:

- current official model documentation lists Function calling as supported;
- current official model documentation lists Structured outputs as supported;
- current official pricing lists Gemini 3.5 Flash-Lite input and output as **Free of charge** on the Free Tier;
- the model ID is currently Stable;
- the probe needs no grounding, caching, media generation, paid priority tier, or other paid-only feature.

The model is hardcoded in `gemini_probe.py`. The probe exposes no model override and has no fallback model list. A failed/free-quota-exhausted request fails or blocks; it never silently selects a paid path.

## Privacy tradeoff recorded before execution

Current Gemini pricing documentation states that Free Tier content may be used to improve Google's products, while paid-tier content is not used for that purpose.

Therefore this Stage 0 probe uses only synthetic fixed strings and no Amodex source code, Black-Islands content, user project content, secrets, private prompts, or personal data.

Production provider policy must expose this privacy property as provider metadata rather than pretending all free tiers have identical data handling.

## Credential boundary

The Gemini API requires authentication through an API key. Current official documentation accepts it in the `x-goog-api-key` request header and recommends secure environment configuration.

The Stage 0 workflow currently has no provider credential available.

Required external setup:

1. create/use a Gemini API key associated with a project that remains on the Free tier;
2. add it to repository Actions secrets as exactly:

```text
GEMINI_API_KEY
```

3. re-run the existing `stage0-provider-gemini` workflow/job on the Stage 0 commit, or push the next P0-03-only change after the secret is configured.

The key should **not** be pasted into chat, committed to Git, added to an issue/PR, or placed in a workflow input.

`workflow_dispatch` is retained for normal use after the workflow exists on the default branch, but GitHub requires a `workflow_dispatch` workflow to exist on the default branch before manual dispatch can originate. During the pre-merge Stage 0 branch, the workflow therefore also has a narrow `push` trigger for its own P0-03 files.

## Probe implementation

`gemini_probe.py` intentionally uses Python stdlib HTTP rather than adding a provider SDK dependency to the Amodex Control Plane just for Stage 0.

It sends exactly two synthetic requests to the fixed free-tier model.

### Test A — forced function calling

The request exposes one function only:

```text
record_probe(value: integer, label: string)
```

Function calling mode is `ANY`, and the only allowed function name is `record_probe`.

Expected structured call:

```json
{
  "name": "record_probe",
  "args": {
    "value": 7,
    "label": "AMODEX_FREE_PROVIDER_OK"
  }
}
```

The probe rejects:

- prose instead of a function call;
- more than one call;
- another function name;
- extra/missing argument keys;
- wrong values.

### Test B — structured final output

The request asks for a schema-constrained JSON object with exactly:

```json
{
  "ok": true,
  "code": "AMODEX_FREE_PROVIDER_OK",
  "value": 7
}
```

The response is parsed again locally and rejects extra/missing/wrong fields even if the provider claims schema conformance.

## Error semantics captured

The probe normalizes at least these external HTTP classes for Stage 0 evidence:

```text
400 -> INVALID_REQUEST
401 -> AUTH_INVALID
403 -> AUTH_FORBIDDEN_OR_TIER
404 -> MODEL_OR_ENDPOINT_NOT_FOUND
408 -> REQUEST_TIMEOUT
409 -> CONFLICT
422 -> UNPROCESSABLE
429 -> RATE_LIMITED
5xx -> PROVIDER_SERVER_ERROR
other 4xx -> PROVIDER_CLIENT_ERROR
```

Only safe response headers such as retry/rate-limit/request IDs may be recorded. The authentication header is never echoed.

Current Gemini documentation says rate limits are tracked across dimensions such as RPM, TPM and RPD, generally per project/tier/model. Actual account-specific limits must be observed rather than hardcoded as universal constants.

## Current external fact baseline — 2026-10-31

Verified from current official Gemini documentation before writing the probe:

- `gemini-3.5-flash-lite` is Stable;
- supports Function calling;
- supports Structured outputs;
- Free Tier input: free of charge;
- Free Tier output: free of charge;
- Gemini Free tier exists without upgrading to paid billing;
- API requests require an API key;
- rate limits vary by model/project/account state and are measured across multiple dimensions.

These external facts must be revalidated when the provider adapter is productionized or when the probe model is changed.

## Why GitHub Models is not used

GitHub Models is not a valid 2026 fallback for this PoC. GitHub retired the GitHub Models service in July 2026, including model inference/API access. Amodex must not design around an already-retired free inference path.

## Workflow behavior

`.github/workflows/stage0-provider-gemini.yml` currently supports:

- a narrow `push` trigger on `stage/0-feasibility` only when the P0-03 probe/workflow files change;
- `workflow_dispatch` for normal post-merge/manual use.

The workflow:

1. checks out the exact Amodex revision with the pinned checkout action;
2. detects whether `GEMINI_API_KEY` is available without printing it;
3. when missing, records `BLOCKED_EXTERNAL_CREDENTIAL`, skips inference and secret-scan steps, and succeeds without fabricating evidence;
4. when present, runs only the fixed free-tier Gemini probe;
5. asserts the secret did not enter tracked content;
6. refuses to modify repository files.

## Executed blocked-state evidence

The branch-safe workflow executed successfully on the Stage 0 branch:

```text
workflow: stage0-provider-gemini
run_id: 41179911310
job_id: 124475017307
commit: 0eab4cfef64ab86d2faa3e9d6263841d292e06d4
conclusion: success
```

Step results:

```text
Checkout exact Amodex revision                 success
Detect external free-tier credential           success
Execute fixed free-tier capability probe       skipped
Assert no secret material entered tracked files skipped
Preserve blocked status without fabricating provider evidence success
```

This is **not provider capability evidence**. It is evidence that the credential boundary fails closed exactly as designed and does not turn a missing secret into a fake green inference result.

## Closure criteria

P0-03 becomes `ADOPT` or `ADAPT` only after a real workflow run records all of:

- external provider request success;
- forced function call schema success;
- structured-output schema success;
- requested model identity;
- observed provider `modelVersion` where returned;
- token usage metadata where returned;
- safe rate-limit/request metadata where returned;
- no paid fallback;
- no credential leakage.

If Free-tier access is unavailable for the configured project/key, the result remains blocked or the Provider Governor design is adapted to another verified free provider. It must never switch to paid inference to make the test green.

## Current decision

**BLOCKED_EXTERNAL_CREDENTIAL.**

The implementation, safety guard and workflow behavior are proven up to the external credential boundary. The only missing P0-03 evidence is a real free-tier provider call after `GEMINI_API_KEY` is configured securely in GitHub Actions.

Because the Stage 0 PoC order is intentionally risk-driven, P0-04 is not being claimed complete ahead of this gate. The repository remains at P0-03 until external provider capability is actually proven or the provider strategy is explicitly revised.
