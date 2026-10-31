"""Google Gemini Developer API adapter for the Stage 1 free-provider path."""

from __future__ import annotations

import json
import httpx

from amodex.runtime.models import ProviderResponse, ProviderUsage
from .base import ProviderFailure


class GeminiProvider:
    provider_name = "gemini"

    def __init__(self, *, api_key: str, timeout_seconds: float = 45.0) -> None:
        if not api_key:
            raise ValueError("Gemini API key is required")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def generate(self, *, model: str, prompt: str, request_key: str) -> ProviderResponse:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        }
        headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=self._timeout_seconds)
        except httpx.TimeoutException as exc:
            raise ProviderFailure(code="REQUEST_TIMEOUT", message="Gemini request timed out", ambiguous_outcome=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure(code="NETWORK_ERROR", message=str(exc), ambiguous_outcome=True) from exc

        if response.status_code >= 400:
            retry_after = response.headers.get("retry-after")
            retry_after_seconds = None
            if retry_after:
                try:
                    retry_after_seconds = float(retry_after)
                except ValueError:
                    pass
            raise ProviderFailure(
                code=_classify_status(response.status_code),
                message=_safe_error_message(response),
                retry_after_seconds=retry_after_seconds,
                ambiguous_outcome=response.status_code >= 500,
            )

        try:
            body = response.json()
            raw = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(raw)
            text = parsed["text"]
            if not isinstance(text, str) or not text:
                raise ValueError("empty text")
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderFailure(code="MALFORMED_PROVIDER_OUTPUT", message="Gemini returned an invalid structured response") from exc

        usage_raw = body.get("usageMetadata") or {}
        return ProviderResponse(
            provider=self.provider_name,
            model=model,
            text=text,
            usage=ProviderUsage(
                prompt_tokens=usage_raw.get("promptTokenCount"),
                completion_tokens=usage_raw.get("candidatesTokenCount"),
                total_tokens=usage_raw.get("totalTokenCount"),
            ),
        )


def _classify_status(status: int) -> str:
    return {400:"INVALID_REQUEST",401:"AUTH_INVALID",403:"AUTH_FORBIDDEN_OR_TIER",404:"MODEL_OR_ENDPOINT_NOT_FOUND",408:"REQUEST_TIMEOUT",409:"CONFLICT",422:"UNPROCESSABLE",429:"RATE_LIMITED"}.get(status, "PROVIDER_SERVER_ERROR" if status >= 500 else "PROVIDER_HTTP_ERROR")


def _safe_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"provider HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"][:500]
    return f"provider HTTP {response.status_code}"
