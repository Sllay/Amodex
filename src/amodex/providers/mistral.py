"""Mistral Studio provider adapter.

The normal text path uses Mistral's Chat Completions endpoint through the shared
OpenAI-compatible adapter. Pixel-grounded visual diagnostics use Mistral's
native image_url string shape with base64 data URLs.
"""

from __future__ import annotations

from amodex.runtime.models import ProviderResponse
from .openai_compatible import OpenAICompatibleProvider
from .vision_chat import generate_json_vision_response


class MistralProvider(OpenAICompatibleProvider):
    provider_name = "mistral"

    def __init__(self, *, api_key: str, timeout_seconds: float = 60.0) -> None:
        super().__init__(
            provider_name=self.provider_name,
            base_url="https://api.mistral.ai/v1",
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
            image_url_shape="string",
            # Mistral's JSON mode is deterministic enough for machine scoring;
            # a fixed seed reduces sampling noise between benchmark reruns.
            payload_overrides={"random_seed": 7, "max_tokens": 256},
        )
