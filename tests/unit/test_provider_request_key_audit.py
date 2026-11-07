"""Observe adapter requests through a local HTTP stub, never a real provider."""
from __future__ import annotations

import json

import httpx
import pytest

from amodex.providers.gemini import GeminiProvider
from amodex.providers.openai_compatible import OpenAICompatibleProvider


@pytest.mark.parametrize("adapter", ["gemini", "openai-compatible"])
def test_request_key_is_not_an_external_deduplication_contract(monkeypatch, adapter):
    requests = []
    def post(url, **kwargs):
        requests.append(kwargs)
        text = json.dumps({"text": "synthetic response"})
        payload = ({"candidates": [{"content": {"parts": [{"text": text}]}}]}
                   if adapter == "gemini" else {"choices": [{"message": {"content": text}}]})
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(httpx, "post", post)
    provider = (GeminiProvider(api_key="synthetic-only") if adapter == "gemini" else
                OpenAICompatibleProvider(provider_name="synthetic", base_url="https://local-fixture.invalid",
                                         api_key="synthetic-only"))
    for key in ["audit-op-one", "audit-op-two"]:
        assert provider.generate(model="synthetic-model", prompt="synthetic prompt", request_key=key).text == "synthetic response"
    assert len(requests) == 2
    assert requests[0]["json"] == requests[1]["json"]
    assert requests[0]["headers"] == requests[1]["headers"]
    assert not any("idempotency" in name.lower() for name in requests[0]["headers"])
