#!/usr/bin/env python3
"""P0-03: prove one real external zero-cost provider path.

The probe intentionally uses only Python stdlib and a fixed Gemini Free Tier
model. It has no fallback path and never logs the API key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROVIDER = "google-gemini"
MODEL = "gemini-3.5-flash-lite"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
EXPECTED_LABEL = "AMODEX_FREE_PROVIDER_OK"
EXPECTED_VALUE = 7
SAFE_HEADER_NAMES = {
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-goog-request-id",
}


class ProbeFailure(RuntimeError):
    pass


class ProviderHTTPError(ProbeFailure):
    def __init__(self, status: int, category: str, detail: str, headers: dict[str, str]):
        super().__init__(f"{category}: HTTP {status}: {detail}")
        self.status = status
        self.category = category
        self.detail = detail
        self.headers = headers


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def classify_http(status: int) -> str:
    return {
        400: "INVALID_REQUEST",
        401: "AUTH_INVALID",
        403: "AUTH_FORBIDDEN_OR_TIER",
        404: "MODEL_OR_ENDPOINT_NOT_FOUND",
        408: "REQUEST_TIMEOUT",
        409: "CONFLICT",
        422: "UNPROCESSABLE",
        429: "RATE_LIMITED",
    }.get(status, "PROVIDER_SERVER_ERROR" if status >= 500 else "PROVIDER_CLIENT_ERROR")


def safe_headers(headers: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if lowered in SAFE_HEADER_NAMES or lowered.startswith("x-ratelimit-"):
            result[lowered] = str(value)
    return result


def post_json(api_key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], float]:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=encoded,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-goog-api-key": api_key,
            "user-agent": "amodex-stage0-p0-03/1",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            elapsed = time.monotonic() - started
            parsed = json.loads(body)
            require(isinstance(parsed, dict), "provider response was not a JSON object")
            return parsed, safe_headers(response.headers), elapsed
    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - started
        raw = exc.read().decode("utf-8", errors="replace")[:3000]
        detail = raw
        try:
            parsed = json.loads(raw)
            detail = str((parsed.get("error") or {}).get("message") or raw)
        except json.JSONDecodeError:
            pass
        raise ProviderHTTPError(
            exc.code,
            classify_http(exc.code),
            detail,
            safe_headers(exc.headers),
        ) from exc
    except urllib.error.URLError as exc:
        raise ProbeFailure(f"NETWORK_ERROR after {elapsed if 'elapsed' in locals() else 0:.3f}s: {exc.reason}") from exc


def first_parts(response: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = response.get("candidates")
    require(isinstance(candidates, list) and candidates, f"no candidates returned: {response.get('promptFeedback')}")
    first = candidates[0]
    require(isinstance(first, dict), "first candidate was not an object")
    content = first.get("content") or {}
    parts = content.get("parts") or []
    require(isinstance(parts, list) and parts, "candidate contained no parts")
    return [part for part in parts if isinstance(part, dict)]


def probe_function_call(api_key: str) -> dict[str, Any]:
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Call record_probe exactly once with value=7 and "
                            f"label='{EXPECTED_LABEL}'. Do not answer in prose."
                        )
                    }
                ],
            }
        ],
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "record_probe",
                        "description": "Record the fixed Stage 0 provider probe values.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "value": {"type": "INTEGER"},
                                "label": {
                                    "type": "STRING",
                                    "enum": [EXPECTED_LABEL],
                                },
                            },
                            "required": ["value", "label"],
                        },
                    }
                ]
            }
        ],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": ["record_probe"],
            }
        },
        "generationConfig": {"temperature": 0, "maxOutputTokens": 128},
    }
    response, headers, elapsed = post_json(api_key, payload)
    calls = [part["functionCall"] for part in first_parts(response) if isinstance(part.get("functionCall"), dict)]
    require(len(calls) == 1, f"expected exactly one functionCall, got {len(calls)}")
    call = calls[0]
    require(call.get("name") == "record_probe", f"unexpected function name: {call.get('name')!r}")
    args = call.get("args")
    require(isinstance(args, dict), "function args were not an object")
    require(set(args) == {"value", "label"}, f"unexpected function arg keys: {sorted(args)}")
    require(args.get("value") == EXPECTED_VALUE, f"wrong function value: {args.get('value')!r}")
    require(args.get("label") == EXPECTED_LABEL, f"wrong function label: {args.get('label')!r}")
    return {
        "passed": True,
        "function_name": call.get("name"),
        "args": args,
        "call_id_present": bool(call.get("id")),
        "model_version": response.get("modelVersion"),
        "response_id": response.get("responseId"),
        "usage": response.get("usageMetadata"),
        "safe_headers": headers,
        "elapsed_seconds": round(elapsed, 3),
    }


def probe_structured_output(api_key: str) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "code": {"type": "string", "enum": [EXPECTED_LABEL]},
            "value": {"type": "integer", "enum": [EXPECTED_VALUE]},
        },
        "required": ["ok", "code", "value"],
        "additionalProperties": False,
    }
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Return the probe object. ok must be true, code must be "
                            f"{EXPECTED_LABEL}, and value must be {EXPECTED_VALUE}."
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 128,
            "responseFormat": {
                "text": {
                    "mimeType": "APPLICATION_JSON",
                    "schema": schema,
                }
            },
        },
    }
    response, headers, elapsed = post_json(api_key, payload)
    texts = [part.get("text") for part in first_parts(response) if isinstance(part.get("text"), str)]
    require(texts, "structured-output response contained no text part")
    parsed = json.loads("".join(texts))
    require(isinstance(parsed, dict), "structured output was not a JSON object")
    require(set(parsed) == {"ok", "code", "value"}, f"unexpected structured keys: {sorted(parsed)}")
    require(parsed.get("ok") is True, f"structured ok was not true: {parsed.get('ok')!r}")
    require(parsed.get("code") == EXPECTED_LABEL, f"wrong structured code: {parsed.get('code')!r}")
    require(parsed.get("value") == EXPECTED_VALUE, f"wrong structured value: {parsed.get('value')!r}")
    return {
        "passed": True,
        "value": parsed,
        "model_version": response.get("modelVersion"),
        "response_id": response.get("responseId"),
        "usage": response.get("usageMetadata"),
        "safe_headers": headers,
        "elapsed_seconds": round(elapsed, 3),
    }


def run_probe(api_key: str) -> dict[str, Any]:
    require(api_key.strip() != "", "GEMINI_API_KEY is empty")
    function_call = probe_function_call(api_key)
    structured_output = probe_structured_output(api_key)
    versions = {
        value
        for value in (
            function_call.get("model_version"),
            structured_output.get("model_version"),
        )
        if isinstance(value, str) and value
    }
    return {
        "status": "PASS",
        "provider": PROVIDER,
        "requested_model": MODEL,
        "zero_cost_guard": {
            "model_is_fixed": True,
            "fallback_models": [],
            "paid_fallback_allowed": False,
        },
        "observed_model_versions": sorted(versions),
        "function_call": function_call,
        "structured_output": structured_output,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print(
            "P0-03 BLOCKED_EXTERNAL_CREDENTIAL: configure a free Gemini API key "
            "as GEMINI_API_KEY; do not paste it into source or logs.",
            file=sys.stderr,
        )
        return 2

    try:
        result = run_probe(api_key)
    except ProviderHTTPError as exc:
        result = {
            "status": "FAIL",
            "provider": PROVIDER,
            "requested_model": MODEL,
            "error": {
                "category": exc.category,
                "http_status": exc.status,
                "detail": exc.detail,
                "safe_headers": exc.headers,
            },
        }
        print(json.dumps(result, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"P0-03 FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
