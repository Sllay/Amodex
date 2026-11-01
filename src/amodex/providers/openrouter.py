"""OpenRouter OpenAI-compatible provider adapter."""

from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    provider_name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 45.0,
        app_title: str = "Amodex",
        app_url: str | None = None,
        require_json_object: bool = False,
    ) -> None:
        headers = {"X-Title": app_title}
        if app_url:
            headers["HTTP-Referer"] = app_url
        super().__init__(
            provider_name=self.provider_name,
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            require_json_object=require_json_object,
            extra_headers=headers,
        )
