from __future__ import annotations

import os
import time

import pytest

from amodex.providers.base import ProviderFailure
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

_TRANSIENT_PROBE_ERRORS = {
    "REQUEST_TIMEOUT",
    "NETWORK_ERROR",
    "PROVIDER_SERVER_ERROR",
}


def _provider(name: str, key: str):
    if name == "groq":
        return GroqProvider(api_key=key, timeout_seconds=30.0)
    if name == "openrouter":
        return OpenRouterProvider(api_key=key, timeout_seconds=30.0)
    if name == "nvidia":
        return NvidiaProvider(api_key=key, timeout_seconds=45.0)
    raise AssertionError(name)


def _probe_once(*, provider_name: str, model: str, client, attempt: int):
    started = time.perf_counter()
    response = client.generate(
        model=model,
        prompt="Reply with a very short confirmation that this zero-cost Amodex provider probe works.",
        request_key=f"stage5-live-{provider_name}-attempt-{attempt}",
    )
    latency_ms = (time.perf_counter() - started) * 1000.0
    return response, latency_ms


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

    # This retry belongs only to the external validation probe, not production
    # request routing. It is deliberately capped at one retry so a transient
    # provider/network timeout cannot create a request storm or hide persistent
    # unavailability.
    last_failure: ProviderFailure | None = None
    for attempt in (1, 2):
        try:
            response, latency_ms = _probe_once(
                provider_name=provider_name,
                model=model,
                client=client,
                attempt=attempt,
            )
            break
        except ProviderFailure as exc:
            last_failure = exc
            if exc.code not in _TRANSIENT_PROBE_ERRORS or attempt == 2:
                raise
            print(
                f"stage5-live transient provider={provider_name} model={model} "
                f"attempt={attempt} error={exc.code}; bounded_retry=1"
            )
            time.sleep(1.0)
    else:  # pragma: no cover - loop either breaks or raises
        assert last_failure is not None
        raise last_failure

    assert response.provider == provider_name
    assert response.model == model
    assert response.text.strip()
    print(
        f"stage5-live provider={provider_name} model={model} attempt={attempt} "
        f"latency_ms={latency_ms:.1f} tokens={response.usage.total_tokens}"
    )
