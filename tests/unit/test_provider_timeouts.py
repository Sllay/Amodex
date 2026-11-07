from __future__ import annotations

import httpx
import os
import pytest

from amodex.providers.timeouts import provider_timeout_for


@pytest.fixture(autouse=True)
def isolated_timeout_overrides(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("AMODEX_PROVIDER_TIMEOUT_"):
            monkeypatch.delenv(name)


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


@pytest.mark.parametrize("model,key", [
    ("mistralai/mistral-large-4-1t-a52b", "MISTRALAI_MISTRAL_LARGE_4_1T_A52B"),
    ("mistral-large-4-1t-a52b", "MISTRAL_LARGE_4_1T_A52B"),
])
def test_complete_and_unnamespaced_identifiers(monkeypatch, model, key):
    monkeypatch.setenv(f"AMODEX_PROVIDER_TIMEOUT_NVIDIA_{key}_READ_SECONDS", "1111")
    assert provider_timeout_for(provider="nvidia", model=model).read_seconds == 1111


@pytest.mark.parametrize("suffix", ["", "_CHAT"])
def test_full_identifier_wins_over_legacy_alias_at_same_specificity(monkeypatch, suffix):
    monkeypatch.setenv(f"AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRAL_LARGE_4_1T_A52B{suffix}_READ_SECONDS", "1111")
    monkeypatch.setenv(f"AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRALAI_MISTRAL_LARGE_4_1T_A52B{suffix}_READ_SECONDS", "1222")
    assert provider_timeout_for(provider="nvidia", model="mistralai/mistral-large-4-1t-a52b").read_seconds == 1222


def test_specific_alias_operation_wins_over_generic_complete_identifier(monkeypatch):
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRAL_LARGE_4_1T_A52B_CHAT_READ_SECONDS", "1111")
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRALAI_MISTRAL_LARGE_4_1T_A52B_READ_SECONDS", "1222")
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_CHAT_READ_SECONDS", "777")
    assert provider_timeout_for(provider="nvidia", model="mistralai/mistral-large-4-1t-a52b").read_seconds == 1111


@pytest.mark.parametrize("model,provider", [
    ("other-publisher/mistral-large-4-1t-a52b", "nvidia"),
    ("mistralai/mistral-large-4-1t-a52b-v2", "nvidia"),
    ("mistralai/mistral-large-4-1t-a52b:free", "nvidia"),
    ("mistralai/mistral-large-4-1t-a52b", "other-provider"),
])
def test_legacy_alias_does_not_leak_to_similar_models_or_other_providers(monkeypatch, model, provider):
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRAL_LARGE_4_1T_A52B_READ_SECONDS", "1111")
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_READ_SECONDS", "123")
    assert provider_timeout_for(provider=provider, model=model).read_seconds == 123


def test_same_basename_in_distinct_namespaces_has_distinct_overrides(monkeypatch):
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_AUTHOR_A_MODEL_READ_SECONDS", "101")
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_AUTHOR_B_MODEL_READ_SECONDS", "202")
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_MODEL_READ_SECONDS", "999")
    assert provider_timeout_for(provider="nvidia", model="author-a/model").read_seconds == 101
    assert provider_timeout_for(provider="nvidia", model="author-b/model").read_seconds == 202
    assert provider_timeout_for(provider="nvidia", model="author-c/model").read_seconds == 900


@pytest.mark.parametrize("component", ["CONNECT", "READ", "WRITE", "POOL"])
def test_all_override_levels_preserve_precedence(monkeypatch, component):
    prefixes = [
        "NVIDIA_MISTRALAI_MISTRAL_LARGE_4_1T_A52B_CHAT_",
        "NVIDIA_MISTRALAI_MISTRAL_LARGE_4_1T_A52B_",
        "NVIDIA_CHAT_", "NVIDIA_", "",
    ]
    for index, prefix in enumerate(prefixes):
        monkeypatch.setenv(f"AMODEX_PROVIDER_TIMEOUT_{prefix}{component}_SECONDS", str(100 + index))
    for index, prefix in enumerate(prefixes):
        policy = provider_timeout_for(provider="nvidia", model="mistralai/mistral-large-4-1t-a52b")
        assert getattr(policy, component.lower() + "_seconds") == 100 + index
        monkeypatch.delenv(f"AMODEX_PROVIDER_TIMEOUT_{prefix}{component}_SECONDS")


@pytest.mark.parametrize("value", ["", "invalid", "nan", "inf", "-inf", "0", "-20"])
def test_invalid_complete_override_falls_back_to_valid_alias(monkeypatch, value):
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRALAI_MISTRAL_LARGE_4_1T_A52B_READ_SECONDS", value)
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_NVIDIA_MISTRAL_LARGE_4_1T_A52B_READ_SECONDS", "1111")
    assert provider_timeout_for(provider="nvidia", model="mistralai/mistral-large-4-1t-a52b").read_seconds == 1111


@pytest.mark.parametrize("value,expected", [("0.01", 1), ("1", 1), ("1800", 1800), ("99999", 1800)])
def test_all_components_are_bounded(monkeypatch, value, expected):
    for component in ["CONNECT", "READ", "WRITE", "POOL"]:
        monkeypatch.setenv(f"AMODEX_PROVIDER_TIMEOUT_{component}_SECONDS", value)
    policy = provider_timeout_for(provider="unlisted", model="unlisted")
    assert policy.as_httpx_timeout() == httpx.Timeout(expected)


@pytest.mark.parametrize("provider,operation,expected", [
    ("nvidia", "chat", 900), ("nvidia", "benchmark", 240),
    ("gemini", "chat", 600), ("gemini", "benchmark", 180),
    ("groq", "chat", 300), ("groq", "benchmark", 120),
    ("openrouter", "chat", 600), ("openrouter", "benchmark", 180),
    ("unknown", "chat", 300),
])
def test_default_budgets_unchanged(provider, operation, expected):
    policy = provider_timeout_for(provider=provider, model="no-override", operation=operation)
    assert (policy.connect_seconds, policy.read_seconds, policy.write_seconds, policy.pool_seconds) == (10, expected, 60, 10)
