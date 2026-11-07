"""Groq OpenAI-compatible provider adapter."""

from __future__ import annotations

import httpx

from typing import Any

from amodex.runtime.models import ProviderResponse
from .openai_compatible import OpenAICompatibleProvider
from .vision_chat import generate_json_vision_response


class GroqProvider(OpenAICompatibleProvider):
    provider_name = "groq"

    def __init__(
        self, *, api_key: str, timeout_seconds: float = 45.0, timeout: httpx.Timeout | None = None
    ) -> None:
        super().__init__(
            provider_name=self.provider_name,
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            timeout=timeout,
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
        overrides: dict[str, Any] = {}
        if model == "qwen/qwen3.8-27b":
            # Keep the visual benchmark focused on perception and deterministic
            # extraction instead of hidden reasoning.
            overrides["reasoning_effort"] = "none"
        return generate_json_vision_response(
            provider_name=self.provider_name,
            base_url=self._base_url,
            api_key=self._api_key,
            timeout_seconds=self._timeout_seconds,
            model=model,
            prompt=prompt,
            image_data=image_data,
            media_type=media_type,
            request_key=request_key,
            image_url_shape="object",
            payload_overrides=overrides,
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
