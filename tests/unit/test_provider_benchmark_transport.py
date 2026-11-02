from __future__ import annotations

import httpx

from amodex.providers.groq import GroqProvider
from amodex.providers.nvidia import NvidiaProvider


def _ok_response(content: str = '{"text":"ok"}') -> httpx.Response:
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
        request=request,
    )


def test_groq_normal_chat_uses_user_only_instruction_without_benchmark_overrides(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        calls.append(json)
        return _ok_response('{"text":"normal answer"}')

    monkeypatch.setattr("amodex.providers.openai_compatible.httpx.post", fake_post)
    response = GroqProvider(api_key="test-key").generate(
        model="openai/gpt-oss-20b",
        prompt="Explain the current project status.",
        request_key="normal-request",
    )

    assert response.text == "normal answer"
    payload = calls[0]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["include_reasoning"] is False
    assert "reasoning_effort" not in payload
    assert "seed" not in payload
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "user"
    assert "field named text" in payload["messages"][0]["content"]
    assert "Explain the current project status." in payload["messages"][0]["content"]


def test_groq_closed_benchmark_probe_is_deterministic(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        calls.append(json)
        return _ok_response('{"text":"AMODEX_TEXT_OK"}')

    monkeypatch.setattr("amodex.providers.openai_compatible.httpx.post", fake_post)
    response = GroqProvider(api_key="test-key").generate(
        model="openai/gpt-oss-20b",
        prompt="Return exactly AMODEX_TEXT_OK and nothing else.",
        request_key="ordinary-uuid-shaped-key",
    )

    assert response.text == "AMODEX_TEXT_OK"
    payload = calls[0]
    assert payload["include_reasoning"] is False
    assert payload["reasoning_effort"] == "low"
    assert payload["seed"] == 7
    assert payload["temperature"] == 0.1
    assert "set text to exactly" in payload["messages"][0]["content"]


def test_groq_benchmark_prefix_also_enables_deterministic_mode(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        calls.append(json)
        return _ok_response('{"text":"custom"}')

    monkeypatch.setattr("amodex.providers.openai_compatible.httpx.post", fake_post)
    GroqProvider(api_key="test-key").generate(
        model="openai/gpt-oss-20b",
        prompt="A future benchmark probe.",
        request_key="benchmark:future-case:123",
    )

    assert calls[0]["reasoning_effort"] == "low"
    assert calls[0]["seed"] == 7


def test_other_json_mode_provider_keeps_system_user_split(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        calls.append(json)
        return _ok_response()

    monkeypatch.setattr("amodex.providers.openai_compatible.httpx.post", fake_post)
    NvidiaProvider(api_key="test-key").generate(
        model="mistralai/mistral-large-4-1t-a52b",
        prompt="hello",
        request_key="r1",
    )

    payload = calls[0]
    assert [item["role"] for item in payload["messages"]] == ["system", "user"]
    assert "include_reasoning" not in payload
    assert "reasoning_effort" not in payload
    assert "seed" not in payload
