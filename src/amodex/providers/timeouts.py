"""Centralized provider request timeout policy.

Timeouts are transport budgets for one upstream request, not task deadlines.
Long-running agent tasks must be checkpointed and resumed by the run orchestrator.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

import httpx

_MIN_TIMEOUT_SECONDS = 1.0
_MAX_TIMEOUT_SECONDS = 1800.0

# Compatibility is deliberately bounded: stripping arbitrary namespaces would
# let an override for one publisher's model affect another publisher's model.
_LEGACY_MODEL_ALIASES = {
    ("nvidia", "mistralai/mistral-large-4-1t-a52b"): "mistral-large-4-1t-a52b",
}

_DEFAULT_READ_SECONDS = {
    ("nvidia", "chat"): 900.0,
    ("nvidia", "benchmark"): 240.0,
    ("gemini", "chat"): 600.0,
    ("gemini", "benchmark"): 180.0,
    ("groq", "chat"): 300.0,
    ("groq", "benchmark"): 120.0,
    ("openrouter", "chat"): 600.0,
    ("openrouter", "benchmark"): 180.0,
}


@dataclass(frozen=True, slots=True)
class ProviderTimeoutPolicy:
    """Independent connect/read/write/pool budgets for one provider request."""

    connect_seconds: float
    read_seconds: float
    write_seconds: float
    pool_seconds: float

    def as_httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_seconds,
            read=self.read_seconds,
            write=self.write_seconds,
            pool=self.pool_seconds,
        )


def provider_timeout_for(
    *,
    provider: str,
    model: str,
    operation: str = "chat",
) -> ProviderTimeoutPolicy:
    """Resolve the most specific environment override, then safe defaults.

    Override precedence (most specific first):
      provider + model + operation
      provider + model
      provider + operation
      provider
      global

    Example:
      AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRAL_LARGE_4_1T_A52B_CHAT_READ_SECONDS=1200
    """

    provider_key = _env_key(provider)
    model_keys = [_env_key(model)]
    alias = _LEGACY_MODEL_ALIASES.get((provider.strip().lower(), model.strip()))
    if alias:
        model_keys.append(_env_key(alias))
    operation_key = _env_key(operation)
    default_read = _DEFAULT_READ_SECONDS.get(
        (provider.strip().lower(), operation.strip().lower()),
        300.0,
    )

    def resolve(component: str, default: float) -> float:
        # Specificity wins first; within the same level the complete model ID
        # wins over its explicitly supported historical alias.
        names = tuple(
            f"AMODEX_PROVIDER_TIMEOUT_{provider_key}_{key}{suffix}_{component.upper()}_SECONDS"
            for suffix in (f"_{operation_key}", "")
            for key in model_keys
        ) + (
            f"AMODEX_PROVIDER_TIMEOUT_{provider_key}_{operation_key}_{component.upper()}_SECONDS",
            f"AMODEX_PROVIDER_TIMEOUT_{provider_key}_{component.upper()}_SECONDS",
            f"AMODEX_PROVIDER_TIMEOUT_{component.upper()}_SECONDS",
        )
        for name in names:
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            if math.isfinite(value) and value > 0:
                return min(_MAX_TIMEOUT_SECONDS, max(_MIN_TIMEOUT_SECONDS, value))
        return default

    return ProviderTimeoutPolicy(
        connect_seconds=resolve("connect", 10.0),
        read_seconds=resolve("read", default_read),
        write_seconds=resolve("write", 60.0),
        pool_seconds=resolve("pool", 10.0),
    )


def _env_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
