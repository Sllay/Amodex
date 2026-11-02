"""Groq OpenAI-compatible provider adapter."""

from __future__ import annotations

from typing import Any

from .openai_compatible import OpenAICompatibleProvider


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
