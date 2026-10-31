"""Deterministic provider used by local/CI integration tests."""

from __future__ import annotations

from amodex.runtime.models import ProviderResponse, ProviderUsage


class DeterministicProvider:
    provider_name = "simulator"

    def __init__(self, *, prefix: str = "simulated") -> None:
        self._prefix = prefix

    def generate(self, *, model: str, prompt: str, request_key: str) -> ProviderResponse:
        return ProviderResponse(
            provider=self.provider_name,
            model=model,
            text=f"{self._prefix}:{prompt}",
            usage=ProviderUsage(prompt_tokens=len(prompt.split()), completion_tokens=1, total_tokens=len(prompt.split()) + 1),
        )
