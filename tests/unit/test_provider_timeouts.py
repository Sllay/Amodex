from __future__ import annotations

import httpx

from amodex.providers.timeouts import provider_timeout_for


def test_nvidia_mistral_chat_gets_long_read_budget(monkeypatch) -> None:
    monkeypatch.delenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_READ_SECONDS", raising=False)
    policy = provider_timeout_for(
        provider="nvidia",
        model="mistralai/mistral-large-4-1t-a52b",
        operation="chat",
    )
    assert policy.read_seconds == 900.0
    timeout = policy.as_httpx_timeout()
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10.0
    assert timeout.read == 900.0


def test_operation_specific_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_BENCHMARK_READ_SECONDS", "333")
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_READ_SECONDS", "777")
    policy = provider_timeout_for(
        provider="nvidia",
        model="mistralai/mistral-large-4-1t-a52b",
        operation="benchmark",
    )
    assert policy.read_seconds == 333.0


def test_model_specific_override_wins_over_provider_default(monkeypatch) -> None:
    monkeypatch.setenv(
        "AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRAL_LARGE_4_1T_A52B_READ_SECONDS",
        "1111",
    )
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_READ_SECONDS", "777")
    policy = provider_timeout_for(
        provider="nvidia",
        model="mistralai/mistral-large-4-1t-a52b",
        operation="chat",
    )
    assert policy.read_seconds == 1111.0


def test_timeout_override_is_bounded_and_invalid_values_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_GEMINI_READ_SECONDS", "99999")
    policy = provider_timeout_for(provider="gemini", model="gemini-test")
    assert policy.read_seconds == 1800.0

    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_GEMINI_READ_SECONDS", "not-a-number")
    policy = provider_timeout_for(provider="gemini", model="gemini-test")
    assert policy.read_seconds == 600.0
