"""NVIDIA NIM OpenAI-compatible provider adapter.

The hosted NVIDIA API Catalog endpoint serves Mistral Large 4 as a multimodal
OpenAI-compatible model. The hosted endpoint reference does not expose an
OpenAI ``response_format`` field for this recipe, so both text and image paths
avoid forcing that field and validate their own output contracts client-side.
"""

from __future__ import annotations

from typing import Any

from amodex.runtime.models import ProviderResponse
from .openai_compatible import OpenAICompatibleProvider
from .vision_chat import generate_json_vision_response


class NvidiaProvider(OpenAICompatibleProvider):
    provider_name = "nvidia"

    def __init__(self, *, api_key: str, timeout_seconds: float = 60.0) -> None:
        super().__init__(
            provider_name=self.provider_name,
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            require_json_object=False,
            extra_headers={"accept": "application/json"},
        )

    def _provider_payload_overrides(
        self,
        *,
        model: str,
        request_key: str,
        benchmark: bool,
    ) -> dict[str, Any]:
        if model == "mistralai/mistral-large-4-1t-a52b":
            # Use the same NVIDIA-hosted reasoning profile that passed a live vision
            # probe. In practice, text benchmarks using `none` returned HTTP 400
            # even though the hosted API reference lists that value.
            return {
                "seed": 7,
                "reasoning_effort": "high",
                "max_tokens": 4096,
            }
        return {}

    def generate_with_image(
        self,
        *,
        model: str,
        prompt: str,
        image_data: bytes,
        media_type: str,
        request_key: str,
    ) -> ProviderResponse:
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
            use_response_format=False,
            extra_headers={"accept": "application/json"},
            payload_overrides={
                "seed": 7,
                "reasoning_effort": "high",
                "max_tokens": 4096,
                "top_p": 1,
                "frequency_penalty": 0,
                "presence_penalty": 0,
            },
        )
