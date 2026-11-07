"""Shared image+text transport for JSON-scored vision benchmarks.

Provider adapters keep their normal text-generation policies while reusing this
strict path for pixel-grounded diagnostics. The helper intentionally accepts
only in-memory image bytes and returns one canonical JSON object as text.
"""

from __future__ import annotations

import base64
import json
import logging
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
_logger = logging.getLogger("amodex.providers.vision")


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    """Parse a provider JSON answer without requiring provider JSON mode.

    Some OpenAI-compatible visual endpoints document structured outputs at the
    model level but do not expose ``response_format`` on the hosted endpoint.
    The benchmark prompt still requires one JSON object. We accept either a raw
    object or a fenced/prose-wrapped object, then canonicalize it before scoring.
    """

    text = raw_text.strip()
    candidates = [text]
    if text.startswith("```") and text.endswith("```"):
        inner = text[3:-3].strip()
        if inner.lower().startswith("json"):
            inner = inner[4:].lstrip()
        candidates.append(inner)
    first = text.find("{")
    last = text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first : last + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("vision response is not a JSON object")


def generate_json_vision_response(
    *,
    provider_name: str,
    base_url: str,
    api_key: str,
    timeout_seconds: float | httpx.Timeout,
    model: str,
    prompt: str,
    image_data: bytes,
    media_type: str,
    request_key: str,
    image_url_shape: ImageUrlShape,
    payload_overrides: Mapping[str, Any] | None = None,
    extra_headers: Mapping[str, str] | None = None,
    use_response_format: bool = True,
) -> ProviderResponse:
    """Send one image request and normalize a machine-scoreable JSON reply."""

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
    }
    if use_response_format:
        payload["response_format"] = {"type": "json_object"}
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
        _logger.warning("vision_upstream_timeout provider=%s model=%s", provider_name, model)
        raise ProviderFailure(
            code="REQUEST_TIMEOUT",
            message=f"{provider_name} vision request timed out",
            ambiguous_outcome=True,
        ) from exc
    except httpx.HTTPError as exc:
        _logger.warning("vision_upstream_network_error provider=%s model=%s", provider_name, model)
        raise ProviderFailure(
            code="NETWORK_TRANSIENT",
            message=str(exc)[:500],
            ambiguous_outcome=True,
        ) from exc

    if response.status_code >= 400:
        message = _safe_error_message(response)
        code = _classify_status(response.status_code, message)
        _logger.warning(
            "vision_upstream_error provider=%s model=%s status=%s code=%s message=%s",
            provider_name,
            model,
            response.status_code,
            code,
            message,
        )
        raise ProviderFailure(
            code=code,
            message=message,
            retry_after_seconds=_retry_after(response),
            ambiguous_outcome=response.status_code >= 500,
            http_status=response.status_code,
        )

    try:
        body = response.json()
        choice = body["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason")
        content = message.get("content")
        if (content is None or content == "") and finish_reason == "length":
            raise ProviderFailure(
                code="PROVIDER_OUTPUT_TRUNCATED",
                message=(
                    f"{provider_name} exhausted max_tokens during reasoning before returning "
                    "a scoreable final answer"
                ),
                retry_after_seconds=0.0,
                http_status=200,
            )
        raw_text = _extract_content_text(content)
        parsed = _parse_json_object(raw_text)
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except ProviderFailure:
        raise
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        finish_reason = None
        try:
            finish_reason = body.get("choices", [{}])[0].get("finish_reason")
        except Exception:  # pragma: no cover - diagnostic only
            pass
        _logger.warning(
            "vision_upstream_malformed provider=%s model=%s status=200 finish_reason=%s",
            provider_name,
            model,
            finish_reason,
        )
        raise ProviderFailure(
            code="MALFORMED_PROVIDER_RESPONSE",
            message=f"{provider_name} returned an unscoreable vision benchmark response",
            http_status=200,
        ) from exc

    usage_raw = body.get("usage") if isinstance(body, dict) else None
    usage = usage_raw if isinstance(usage_raw, dict) else {}
    total_tokens = _optional_int(usage.get("total_tokens"))
    _logger.info(
        "vision_upstream_ok provider=%s model=%s status=200 finish_reason=%s total_tokens=%s",
        provider_name,
        model,
        finish_reason,
        total_tokens,
    )
    return ProviderResponse(
        provider=provider_name,
        model=model,
        text=canonical,
        usage=ProviderUsage(
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            total_tokens=total_tokens,
        ),
    )
