"""NVIDIA NIM OpenAI-compatible provider adapter.

The hosted NVIDIA API Catalog endpoint serves Mistral Large 4 as a multimodal
OpenAI-compatible model. Text requests use the shared compatibility adapter;
visual benchmark requests reuse the strict JSON image transport.
"""

from __future__ import annotations

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
            require_json_object=True,
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
            # NVIDIA's API Catalog sample uses the OpenAI object form:
            # {"type":"image_url","image_url":{"url":"data:..."}}.
            image_url_shape="object",
            # Mistral Large 4 always reasons on NVIDIA NIM; "none" is invalid.
            # Keep a fixed seed for repeatability and enough output budget for
            # reasoning plus the tiny JSON answer used by the benchmark.
            payload_overrides={
                "seed": 7,
                "reasoning_effort": "high",
                "max_tokens": 1024,
            },
        )
