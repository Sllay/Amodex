"""Groq OpenAI-compatible provider adapter."""

from __future__ import annotations

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
        )
