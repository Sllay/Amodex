"""Strict adapter for OpenAI-compatible chat-completion providers.

Used by Groq, OpenRouter and NVIDIA. Provider policy remains outside this module;
constructing an adapter is not equivalent to Governor admission.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from amodex.runtime.models import ProviderResponse, ProviderUsage
from .base import ProviderFailure


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 45.0,
        require_json_object: bool = True,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        if not provider_name.strip():
            raise ValueError("provider_name is required")
        if not base_url.startswith("https://"):
            raise ValueError("provider base_url must be HTTPS")
        if not api_key:
            raise ValueError(f"{provider_name} API key is required")
        self.provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._require_json_object = require_json_object
        self._extra_headers = dict(extra_headers or {})

    def generate(self, *, model: str, prompt: str, request_key: str) -> ProviderResponse:
        if not model.strip():
            raise ValueError("model is required")
        if not prompt.strip():
            raise ValueError("prompt is required")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return a useful answer. If JSON mode is enabled, return exactly "
                        "one JSON object with a non-empty string field named text."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        if self._require_json_object:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
            **self._extra_headers,
        }
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderFailure(
                code="REQUEST_TIMEOUT",
                message=f"{self.provider_name} request timed out",
                ambiguous_outcome=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure(
                code="NETWORK_TRANSIENT",
                message=str(exc)[:500],
                ambiguous_outcome=True,
            ) from exc

        if response.status_code >= 400:
            raise ProviderFailure(
                code=_classify_status(response.status_code, _safe_error_message(response)),
                message=_safe_error_message(response),
                retry_after_seconds=_retry_after(response),
                ambiguous_outcome=response.status_code >= 500,
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            raw_text = _extract_content_text(content)
            if self._require_json_object:
                parsed = json.loads(raw_text)
                text = parsed["text"]
            else:
                text = raw_text
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty provider text")
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderFailure(
                code="MALFORMED_PROVIDER_RESPONSE",
                message=f"{self.provider_name} returned an invalid response",
            ) from exc

        usage_raw = body.get("usage") if isinstance(body, dict) else None
        usage = usage_raw if isinstance(usage_raw, dict) else {}
        return ProviderResponse(
            provider=self.provider_name,
            model=model,
            text=text.strip(),
            usage=ProviderUsage(
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                total_tokens=_optional_int(usage.get("total_tokens")),
            ),
        )


def _extract_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return "".join(parts)
    raise ValueError("unsupported message content")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _safe_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"provider HTTP {response.status_code}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:500]
        if isinstance(error, str):
            return error[:500]
        if isinstance(body.get("message"), str):
            return str(body["message"])[:500]
    return f"provider HTTP {response.status_code}"


def _classify_status(status: int, message: str) -> str:
    lowered = message.lower()
    if status == 400:
        if "context" in lowered and any(word in lowered for word in ("large", "length", "window", "token")):
            return "CONTEXT_TOO_LARGE"
        return "REQUEST_INVALID"
    if status == 401:
        return "AUTH_INVALID"
    if status == 402:
        return "PAYMENT_REQUIRED_OR_BALANCE"
    if status == 403:
        if any(word in lowered for word in ("quota", "limit", "capacity")):
            return "QUOTA_EXHAUSTED_WINDOW"
        return "AUTH_FORBIDDEN"
    if status == 404:
        return "MODEL_NOT_FOUND"
    if status in {408, 504}:
        return "PROVIDER_TIMEOUT"
    if status in {409, 422}:
        return "REQUEST_INVALID"
    if status == 429:
        if "concurr" in lowered:
            return "CONCURRENCY_LIMITED"
        if "quota" in lowered or "daily" in lowered:
            return "QUOTA_EXHAUSTED_WINDOW"
        return "RATE_LIMITED"
    if status in {502, 503}:
        return "PROVIDER_OVERLOADED"
    if status >= 500:
        return "PROVIDER_INTERNAL"
    return "UNKNOWN_PROVIDER_ERROR"
