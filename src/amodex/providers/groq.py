"""Groq OpenAI-compatible provider adapter."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from amodex.runtime.models import ProviderResponse, ProviderUsage
from .base import ProviderFailure
from .openai_compatible import (
    OpenAICompatibleProvider,
    _classify_status,
    _retry_after,
    _safe_error_message,
)


class GroqProvider(OpenAICompatibleProvider):
    provider_name = "groq"

    def __init__(self, *, api_key: str, timeout_seconds: float = 45.0) -> None:
        super().__init__(
            provider_name=self.provider_name,
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            require_json_object=True,
            # GPT-OSS follows instructions more reliably when the complete
            # instruction set lives in the user message instead of being split
            # across system/user roles.
            user_only_instructions=True,
        )

    def generate_with_image(
        self,
        *,
        model: str,
        prompt: str,
        image_data: bytes,
        media_type: str,
        request_key: str,
    ) -> ProviderResponse:
        """Execute one pixel-grounded Groq vision request.

        This path is deliberately separate from normal text generation. It is
        used by the measured vision benchmark and returns canonical JSON text so
        deterministic scorers can inspect fields without parsing provider prose.
        """

        if not model.strip():
            raise ValueError("model is required")
        if not prompt.strip():
            raise ValueError("prompt is required")
        if not image_data:
            raise ValueError("image_data is required")
        if not media_type.startswith("image/"):
            raise ValueError("vision input must use image/* media type")

        encoded = base64.b64encode(image_data).decode("ascii")
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
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                        },
                    ],
                }
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        if model == "qwen/qwen3.8-27b":
            # Current Groq docs list `none` as the default/supported Qwen 3.8
            # reasoning profile. Keep the benchmark focused on perception and
            # deterministic structured extraction rather than hidden reasoning.
            payload["reasoning_effort"] = "none"

        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
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
                message="groq vision request timed out",
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
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, dict) and item.get("text")
                )
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty vision response")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("vision response is not a JSON object")
            canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderFailure(
                code="MALFORMED_PROVIDER_RESPONSE",
                message="groq returned an invalid vision benchmark response",
            ) from exc

        usage_raw = body.get("usage") if isinstance(body, dict) else None
        usage = usage_raw if isinstance(usage_raw, dict) else {}

        def optional_int(value: object) -> int | None:
            if value is None or isinstance(value, bool):
                return None
            try:
                parsed_value = int(value)
            except (TypeError, ValueError):
                return None
            return parsed_value if parsed_value >= 0 else None

        return ProviderResponse(
            provider=self.provider_name,
            model=model,
            text=canonical,
            usage=ProviderUsage(
                prompt_tokens=optional_int(usage.get("prompt_tokens")),
                completion_tokens=optional_int(usage.get("completion_tokens")),
                total_tokens=optional_int(usage.get("total_tokens")),
            ),
        )

    def _provider_payload_overrides(
        self,
        *,
        model: str,
        request_key: str,
        benchmark: bool,
    ) -> dict[str, Any]:
        if not model.startswith("openai/gpt-oss-"):
            return {}

        # Amodex consumes only the final answer, not the provider's reasoning
        # trace. Keeping reasoning out of the response also avoids contaminating
        # exact-output benchmark measurements.
        overrides: dict[str, Any] = {"include_reasoning": False}
        if benchmark:
            # Benchmarks should measure capability with reproducible, low-noise
            # output. Normal chat responses keep the provider's default reasoning
            # effort and are not forced to this deterministic profile.
            overrides.update(
                {
                    "reasoning_effort": "low",
                    "seed": 7,
                }
            )
        return overrides
