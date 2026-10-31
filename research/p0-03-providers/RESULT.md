# P0-03 — Real Zero-Cost Provider Capability Probe

Status: **ADOPT**

P0-03 proved that Amodex can execute a real external provider path with a fixed zero-cost model, strict structured/tool-call validation, no paid fallback, and no credential leakage.

## Provider under test

Provider: Google Gemini Developer API

Fixed probe model:

```text
gemini-3.5-flash-lite
```

The model is hardcoded in `gemini_probe.py`. The probe exposes no model override and has no fallback model list.

```text
fallback_models: []
paid_fallback_allowed: false
model_is_fixed: true
```

A failed request or exhausted free quota must fail/block. The probe never silently selects another model or a paid path.

## Privacy boundary

The probe uses only synthetic fixed strings. It sends no Amodex source code, Black-Islands content, user project content, secrets, private prompts, or personal data.

Provider privacy/data-handling properties must become explicit Provider Governor metadata instead of assuming all free tiers are equivalent.

## Credential boundary

The credential is supplied only through the repository Actions secret:

```text
GEMINI_API_KEY
```

The key is never committed, printed, added to workflow inputs, PR text, or result files.

The workflow first proved the missing-secret fail-closed path, then was re-run after the owner configured the secret.

## Probe implementation

`gemini_probe.py` intentionally uses Python stdlib HTTP so Stage 0 does not add a provider SDK dependency to the Amodex Control Plane.

It performs exactly two synthetic provider requests.

### Test A — forced function calling

The model receives one allowed function:

```text
record_probe(value: integer, label: string)
```

Function calling mode is forced and the expected call is:

```json
{
  "name": "record_probe",
  "args": {
    "value": 7,
    "label": "AMODEX_FREE_PROVIDER_OK"
  }
}
```

Observed result:

```text
function_name: record_probe
value: 7
label: AMODEX_FREE_PROVIDER_OK
call_id_present: true
model_version: gemini-3.5-flash-lite
elapsed_seconds: 6.334
passed: true
```

Observed token usage:

```text
promptTokenCount: 103
candidatesTokenCount: 28
totalTokenCount: 131
serviceTier: standard
```

### Test B — structured final output

The response is constrained to and then locally revalidated as:

```json
{
  "ok": true,
  "code": "AMODEX_FREE_PROVIDER_OK",
  "value": 7
}
```

Observed result:

```text
ok: true
code: AMODEX_FREE_PROVIDER_OK
value: 7
model_version: gemini-3.5-flash-lite
elapsed_seconds: 5.304
passed: true
```

Observed token usage:

```text
promptTokenCount: 31
candidatesTokenCount: 33
totalTokenCount: 64
serviceTier: standard
```

## Real workflow evidence

Workflow:

```text
stage0-provider-gemini
```

Re-run after `GEMINI_API_KEY` was configured:

```text
run_id: 41179911310
job_id: 124484705703
checkout commit: 0eab4cfef64ab86d2faa3e9d6263841d292e06d4
conclusion: success
```

Step results:

```text
Checkout exact Amodex revision                  success
Detect external free-tier credential            success
Execute fixed free-tier capability probe        success
Assert no secret material entered tracked files success
Preserve blocked status                         skipped
```

Provider result:

```text
provider: google-gemini
requested_model: gemini-3.5-flash-lite
observed_model_versions:
  - gemini-3.5-flash-lite
status: PASS
```

The provider returned the same model version requested. No fallback model was used.

## Credential-leakage evidence

The workflow scanned tracked content for the exact secret value and then required a clean Git diff.

Result:

```text
secret scan: PASS
git diff: clean
```

The credential remained masked by GitHub Actions and did not enter tracked files.

## Error semantics retained for the Provider Governor

The probe normalizes external HTTP classes into stable Amodex categories:

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

Actual quota/rate-limit numbers are provider/project/account state and must not be hardcoded as universal constants.

## Initial blocked-state evidence retained

Before the secret existed, the same workflow proved the fail-closed credential boundary:

```text
run_id: 41179911310
initial job_id: 124475017307
credential detection: success
provider probe: skipped
secret scan: skipped
blocked-state preservation: success
```

That run was intentionally not counted as provider capability evidence. It remains useful proof that missing credentials do not become a fake green inference result.

## Decision

**ADOPT** the provider capability contract demonstrated by this PoC.

This does **not** mean the production Gemini adapter is already implemented. It freezes the following Stage 0 conclusions:

- a real zero-cost external provider path is feasible;
- function/tool calling can satisfy strict schema checks;
- schema-constrained final output can satisfy strict local validation;
- provider/model identity and token usage can be observed;
- paid fallback can remain disabled;
- credentials can stay outside source and result artifacts;
- provider errors can be normalized into Provider Governor semantics;
- free-tier privacy properties must be represented explicitly in provider metadata.

## Next gate

P0-03 is closed. Stage 0 may now proceed to:

```text
P0-04 — PostgreSQL leases and fencing
```

P0-04 must prove durable authority, lease expiry/reacquisition, monotonically safe fencing behavior, stale-worker rejection, and recovery semantics before the Git reconciliation PoC begins.
