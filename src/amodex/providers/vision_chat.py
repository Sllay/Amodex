"""Shared image+text transport for JSON-scored vision benchmarks.

Provider adapters keep their normal text-generation policies while reusing this
strict path for pixel-grounded diagnostics. The helper intentionally accepts
only in-memory image bytes and returns one canonical JSON object as text.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from amodex.runtime.models import ProviderResponse, ProviderUsage
from .base import ProviderFailure
from .openai_compatible import (
    _classify_status,
    _extract_content_text,
    _optional_int,
    _retry_after,
    _safe_error_message,
)


ImageUrlShape = Literal["object", "string"]


def generate_json_vision_response(
    *,
    provider_name: str,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
    model: str,
    prompt: str,
    image_data: bytes,
    media_type: str,
    request_key: str,
    image_url_shape: ImageUrlShape,
    payload_overrides: Mapping[str, Any] | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> ProviderResponse:
    """Send one deterministic image request and normalize a JSON-object reply."""

    if not provider_name.strip():
        raise ValueError("provider_name is required")
    if not base_url.startswith("https://"):
        raise ValueError("provider base_url must be HTTPS")
    if not api_key:
        raise ValueError(f"{provider_name} API key is required")
    if not model.strip():
        raise ValueError("model is required")
    if not prompt.strip():
        raise ValueError("prompt is required")
    if not image_data:
        raise ValueError("image_data is required")
    if not media_type.startswith("image/"):
        raise ValueError("vision input must use image/* media type")
    if not request_key.strip():
        raise ValueError("request_key is required")

    encoded = base64.b64encode(image_data).decode("ascii")
    data_url = f"data:{media_type};base64,{encoded}"
    image_url: object
    if image_url_shape == "object":
        image_url = {"url": data_url}
    elif image_url_shape == "string":
        image_url = data_url
    else:  # pragma: no cover - Literal plus defensive runtime validation
        raise ValueError(f"unsupported image_url_shape: {image_url_shape}")

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "This is a machine-scored vision benchmark. Inspect the image pixels, "
                            "not filenames or metadata. Return only one valid JSON object, with no "
                            "markdown or commentary.\n\nTask:\n" + prompt
                        ),
                    },
                    {"type": "image_url", "image_url": image_url},
                ],
            }
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    payload.update(dict(payload_overrides or {}))

    headers = {
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
        **dict(extra_headers or {}),
    }
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        raise ProviderFailure(
            code="REQUEST_TIMEOUT",
            message=f"{provider_name} vision request timed out",
            ambiguous_outcome=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderFailure(
            code="NETWORK_TRANSIENT",
            message=str(exc)[:500],
            ambiguous_outcome=True,
        ) from exc

    if response.status_code >= 400:
        message = _safe_error_message(response)
        raise ProviderFailure(
            code=_classify_status(response.status_code, message),
            message=message,
            retry_after_seconds=_retry_after(response),
            ambiguous_outcome=response.status_code >= 500,
        )

    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        raw_text = _extract_content_text(content)
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            raise ValueError("vision response is not a JSON object")
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderFailure(
            code="MALFORMED_PROVIDER_RESPONSE",
            message=f"{provider_name} returned an invalid vision benchmark response",
        ) from exc

    usage_raw = body.get("usage") if isinstance(body, dict) else None
    usage = usage_raw if isinstance(usage_raw, dict) else {}
    return ProviderResponse(
        provider=provider_name,
        model=model,
        text=canonical,
        usage=ProviderUsage(
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            total_tokens=_optional_int(usage.get("total_tokens")),
        ),
    )
