"""Provider interfaces and normalized failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from amodex.runtime.models import ProviderResponse


@dataclass(slots=True)
class ProviderFailure(Exception):
    """Normalized provider failure that remains a normal mutable Exception.

    Exceptions must allow Python/runtime machinery to assign traceback/context
    attributes. Freezing an Exception dataclass breaks that behavior on Python
    3.13 during timeout/error propagation.
    """

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
