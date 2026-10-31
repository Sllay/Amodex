"""Provider interfaces and normalized failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from amodex.runtime.models import ProviderResponse


@dataclass(frozen=True, slots=True)
class ProviderFailure(Exception):
    code: str
    message: str
    retry_after_seconds: float | None = None
    ambiguous_outcome: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ProviderClient(Protocol):
    provider_name: str

    def generate(self, *, model: str, prompt: str, request_key: str) -> ProviderResponse:
        ...
