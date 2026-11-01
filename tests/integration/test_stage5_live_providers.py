from __future__ import annotations

import os
import time

import pytest

from amodex.providers.groq import GroqProvider
from amodex.providers.nvidia import NvidiaProvider
from amodex.providers.openrouter import OpenRouterProvider


PROBES = (
    (
        "groq",
        "GROQ_API_KEY",
        "AMODEX_GROQ_BILLING_CLASS",
        "AMODEX_GROQ_MODEL",
        "openai/gpt-oss-20b",
    ),
    (
        "openrouter",
        "OPENROUTER_API_KEY",
        "AMODEX_OPENROUTER_BILLING_CLASS",
        "AMODEX_OPENROUTER_MODEL",
        "openrouter/free",
    ),
    (
        "nvidia",
        "NVIDIA_API_KEY",
        "AMODEX_NVIDIA_BILLING_CLASS",
        "AMODEX_NVIDIA_MODEL",
        "mistralai/mistral-large-4-1t-a52b",
    ),
)


def _provider(name: str, key: str):
    if name == "groq":
        return GroqProvider(api_key=key, timeout_seconds=30.0)
    if name == "openrouter":
        return OpenRouterProvider(api_key=key, timeout_seconds=30.0)
    if name == "nvidia":
        return NvidiaProvider(api_key=key, timeout_seconds=45.0)
    raise AssertionError(name)


@pytest.mark.parametrize("provider_name,key_env,billing_env,model_env,default_model", PROBES)
def test_configured_live_free_provider_probe(
    provider_name: str,
    key_env: str,
    billing_env: str,
    model_env: str,
    default_model: str,
) -> None:
    key = os.environ.get(key_env, "")
    billing = os.environ.get(billing_env, "unknown").strip().lower()
    if not key:
        pytest.skip(f"{key_env} is not configured")
    if billing != "free":
        pytest.skip(
            f"{provider_name} live call denied: {billing_env} must explicitly be 'free'"
        )

    model = os.environ.get(model_env, default_model).strip() or default_model
    client = _provider(provider_name, key)
    started = time.perf_counter()
    response = client.generate(
        model=model,
        prompt="Reply with a very short confirmation that this zero-cost Amodex provider probe works.",
        request_key=f"stage5-live-{provider_name}",
    )
    latency_ms = (time.perf_counter() - started) * 1000.0

    assert response.provider == provider_name
    assert response.model == model
    assert response.text.strip()
    print(
        f"stage5-live provider={provider_name} model={model} latency_ms={latency_ms:.1f} "
        f"tokens={response.usage.total_tokens}"
    )
