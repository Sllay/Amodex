from __future__ import annotations

import httpx
import pytest

from amodex.providers.base import ProviderFailure
from amodex.providers.capabilities import BillingClass, ModelLifecycle
from amodex.providers.catalog import stage5_declared_candidates
from amodex.providers.groq import GroqProvider
from amodex.providers.nvidia import NvidiaProvider
from amodex.providers.openrouter import OpenRouterProvider
from amodex.providers.registry import (
    CredentialPoolConfig,
    ProviderExecutionDenied,
    ProviderExecutionRegistry,
)


def _response(status: int, *, json_body: dict, headers: dict[str, str] | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    return httpx.Response(status, json=json_body, headers=headers or {}, request=request)


def test_openai_compatible_adapters_use_expected_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        calls.append((url, headers, json))
        content = "plain-openrouter" if "openrouter.ai" in url else '{"text":"structured-ok"}'
        return _response(
            200,
            json_body={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

    monkeypatch.setattr("amodex.providers.openai_compatible.httpx.post", fake_post)

    groq = GroqProvider(api_key="groq-secret")
    openrouter = OpenRouterProvider(api_key="or-secret")
    nvidia = NvidiaProvider(api_key="nv-secret")

    groq_response = groq.generate(model="openai/gpt-oss-20b", prompt="hello", request_key="r1")
    or_response = openrouter.generate(model="openrouter/free", prompt="hello", request_key="r2")
    nv_response = nvidia.generate(
        model="mistralai/mistral-large-4-1t-a52b", prompt="hello", request_key="r3"
    )

    assert groq_response.text == "structured-ok"
    assert or_response.text == "plain-openrouter"
    assert nv_response.text == "structured-ok"
    assert groq_response.usage.total_tokens == 6
    assert calls[0][0] == "https://api.groq.com/openai/v1/chat/completions"
    assert calls[1][0] == "https://openrouter.ai/api/v1/chat/completions"
    assert calls[2][0] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert calls[0][1]["authorization"] == "Bearer groq-secret"
    assert calls[1][1]["X-Title"] == "Amodex"
    assert calls[0][2]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1][2]
    assert calls[2][2]["response_format"] == {"type": "json_object"}


def test_provider_errors_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    def rate_limited(*args, **kwargs):
        return _response(
            429,
            json_body={"error": {"message": "rate limit reached"}},
            headers={"retry-after": "3"},
        )

    monkeypatch.setattr("amodex.providers.openai_compatible.httpx.post", rate_limited)
    with pytest.raises(ProviderFailure) as raised:
        GroqProvider(api_key="key").generate(
            model="openai/gpt-oss-20b", prompt="hello", request_key="r"
        )
    assert raised.value.code == "RATE_LIMITED"
    assert raised.value.retry_after_seconds == 3.0

    def payment(*args, **kwargs):
        return _response(402, json_body={"error": {"message": "balance required"}})

    monkeypatch.setattr("amodex.providers.openai_compatible.httpx.post", payment)
    with pytest.raises(ProviderFailure) as raised_payment:
        NvidiaProvider(api_key="key").generate(
            model="mistralai/mistral-large-4-1t-a52b", prompt="hello", request_key="r"
        )
    assert raised_payment.value.code == "PAYMENT_REQUIRED_OR_BALANCE"


def test_execution_registry_resolves_secrets_only_after_free_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.provider == "groq")
    candidate = type(candidate)(
        provider=candidate.provider,
        model=candidate.model,
        credential_pool_id=candidate.credential_pool_id,
        cost_class=candidate.cost_class,
        capabilities=candidate.capabilities,
        context_capacity_tokens=candidate.context_capacity_tokens,
        lifecycle=ModelLifecycle.ELIGIBLE,
        provenance=candidate.provenance,
        enabled=True,
    )
    monkeypatch.setenv("GROQ_TEST_SECRET", "secret-value")
    free_registry = ProviderExecutionRegistry(
        (
            CredentialPoolConfig(
                pool_id="groq-free-primary",
                provider="groq",
                billing_class=BillingClass.FREE,
                secret_env="GROQ_TEST_SECRET",
            ),
        )
    )
    client = free_registry.client_for(candidate)
    assert client.provider_name == "groq"
    assert "secret-value" not in repr(free_registry)

    paid_registry = ProviderExecutionRegistry(
        (
            CredentialPoolConfig(
                pool_id="groq-free-primary",
                provider="groq",
                billing_class=BillingClass.PAID,
                secret_env="GROQ_TEST_SECRET",
            ),
        )
    )
    with pytest.raises(ProviderExecutionDenied):
        paid_registry.client_for(candidate)

    monkeypatch.delenv("GROQ_TEST_SECRET")
    with pytest.raises(ProviderExecutionDenied):
        free_registry.client_for(candidate)
