"""No-provider-call regressions for owner-only NVIDIA connectivity diagnostics."""

from __future__ import annotations

import base64
import json
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from amodex.api.model_intelligence_routes import build_model_intelligence_router
from amodex.model_intelligence.nvidia_diagnostics import (
    MODEL, ROUTE_ID, TEXT_SENTINEL, URL, VISION_SENTINEL, NvidiaUpstreamDiagnostics,
)
from amodex.providers.capabilities import BillingClass
from amodex.providers.registry import CredentialPoolConfig, ProviderExecutionRegistry


class Repo:
    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled
        self.reads = 0

    def get_route(self, route_id):
        assert route_id == ROUTE_ID
        self.reads += 1
        return {"route_id": ROUTE_ID, "enabled": self.enabled}


class Service:
    def __init__(self, *, free=True, enabled=True):
        self._repository = Repo(enabled=enabled)
        self._execution_registry = ProviderExecutionRegistry(
            (CredentialPoolConfig(
                pool_id="nvidia-free-primary",
                provider="nvidia",
                billing_class=BillingClass.FREE if free else BillingClass.UNKNOWN,
                secret_env="NVIDIA_API_KEY",
            ),)
        )


@pytest.fixture(autouse=True)
def configured_env(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-super-secret-test-value")
    monkeypatch.setenv("AMODEX_NVIDIA_BILLING_CLASS", "free")


def response(status, body):
    return httpx.Response(
        status,
        request=httpx.Request("POST", URL),
        json=body,
    )


def test_configuration_reads_key_presence_without_provider_network_or_secret(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("configuration must not contact provider")
    monkeypatch.setattr(httpx, "post", no_network)
    config = NvidiaUpstreamDiagnostics(Service()).configuration()
    assert config["admission_ready"] is True
    assert config["active_route"] is True
    assert config["pool_billing"] == "free"
    assert config["key_present"] is True
    assert config["key_looks_like_nvidia"] is True
    assert "nvapi-super-secret" not in json.dumps(config)


def test_live_text_probe_is_minimal_documented_nvidia_request(monkeypatch):
    received = {}
    def fake_post(url, *, headers, json, timeout):
        received.update(url=url, headers=headers, body=json, timeout=timeout)
        return response(200, {
            "choices": [{"finish_reason": "stop", "message": {"content": TEXT_SENTINEL}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        })
    monkeypatch.setattr(httpx, "post", fake_post)
    diagnostic = NvidiaUpstreamDiagnostics(Service())
    result = diagnostic.run(mode="text")
    assert result["upstream_status"] == 200
    assert result["outcome"] == "PASS"
    assert result["exact_match"] is True
    assert result["usage"]["total_tokens"] == 28
    assert result["provider_call_performed"] is True
    assert received["url"] == URL
    assert received["headers"]["Authorization"] == "Bearer nvapi-super-secret-test-value"
    assert received["body"]["model"] == MODEL
    assert received["body"]["reasoning_effort"] == "none"
    assert received["body"]["max_tokens"] == 256
    assert "response_format" not in received["body"]
    assert isinstance(received["body"]["messages"][0]["content"], str)
    assert "nvapi-super-secret" not in json.dumps(result)
    retry = diagnostic.run(mode="text")
    assert retry["outcome"] == "COOLDOWN"
    assert retry["provider_call_performed"] is False


def test_vision_probe_really_sends_png_in_documented_image_url_shape(monkeypatch):
    seen = {}
    def fake_post(url, *, headers, json, timeout):
        seen.update(payload=json)
        return response(200, {
            "choices": [{"finish_reason": "stop", "message": {
                "reasoning_content": "I can read it",
                "content": VISION_SENTINEL,
            }}],
            "usage": {"total_tokens": 202},
        })
    monkeypatch.setattr(httpx, "post", fake_post)
    result = NvidiaUpstreamDiagnostics(Service()).run(mode="vision")
    body = seen["payload"]
    assert result["outcome"] == "PASS"
    assert result["upstream_status"] == 200
    assert result["reasoning_present"] is True
    assert result["request"]["has_image"] is True
    assert result["request"]["image_bytes"] > 100
    assert body["reasoning_effort"] == "high"
    assert body["max_tokens"] == 4096
    assert body["stream"] is False
    assert "response_format" not in body
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    raw = base64.b64decode(content[1]["image_url"]["url"].removeprefix("data:image/png;base64,"))
    assert raw.startswith(b"\\x89PNG\\r\\n\\x1a\\n")
    assert len(raw) == result["request"]["image_bytes"]
    assert "image_url" not in json.dumps(result)  # Never return pixel data to client


def test_auth_401_is_explicit_not_confused_with_amodex_http_200(monkeypatch):
    def fake_post(*args, **kwargs):
        return response(401, {"error": {"message": "Missing or invalid NVIDIA API key nvapi-super-secret-test-value"}})
    monkeypatch.setattr(httpx, "post", fake_post)
    result = NvidiaUpstreamDiagnostics(Service()).run(mode="text")
    assert result["upstream_status"] == 401
    assert result["outcome"] == "AUTH_INVALID"
    assert result["provider_call_performed"] is True
    assert "nvapi-super-secret" not in json.dumps(result)
    assert result["upstream_error_excerpt"]


def test_http_200_null_reasoning_length_is_truncated_not_auth_failure(monkeypatch):
    def fake_post(*args, **kwargs):
        return response(200, {
            "choices": [{"finish_reason": "length", "message": {
                "content": None, "reasoning_content": "thought for too long",
            }}]
        })
    monkeypatch.setattr(httpx, "post", fake_post)
    result = NvidiaUpstreamDiagnostics(Service()).run(mode="vision")
    assert result["upstream_status"] == 200
    assert result["outcome"] == "OUTPUT_TRUNCATED_IN_REASONING"
    assert result["content_present"] is False
    assert result["reasoning_present"] is True
    assert result["finish_reason"] == "length"


def test_http_200_wrong_answer_is_not_transmission_failure(monkeypatch):
    def fake_post(*args, **kwargs):
        return response(200, {"choices": [{"finish_reason": "stop", "message": {"content": "not the correct text"}}]})
    monkeypatch.setattr(httpx, "post", fake_post)
    result = NvidiaUpstreamDiagnostics(Service()).run(mode="vision")
    assert result["outcome"] == "ANSWER_MISMATCH"
    assert result["upstream_status"] == 200


def test_unknown_billing_and_retired_route_never_call_provider(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("must fail closed before provider")
    monkeypatch.setattr(httpx, "post", no_network)
    result = NvidiaUpstreamDiagnostics(Service(free=False)).run(mode="text")
    assert result["outcome"] == "LOCAL_ADMISSION_DENIED"
    assert result["provider_call_performed"] is False
    result = NvidiaUpstreamDiagnostics(Service(enabled=False)).run(mode="vision")
    assert result["outcome"] == "LOCAL_ADMISSION_DENIED"
    assert result["provider_call_performed"] is False


def test_private_gate_required_even_for_status(monkeypatch):
    monkeypatch.delenv("AMODEX_ACCESS_PASSCODE", raising=False)
    app = FastAPI()
    app.include_router(build_model_intelligence_router(Service()))
    client = TestClient(app)
    assert client.get("/v1/model-intelligence/diagnostics/nvidia").status_code == 404
    assert client.post("/v1/model-intelligence/diagnostics/nvidia/run", json={"mode": "vision"}).status_code == 404


def test_private_diagnostics_response_is_no_store(monkeypatch):
    monkeypatch.setenv("AMODEX_ACCESS_PASSCODE", "123456")
    app = FastAPI()
    app.include_router(build_model_intelligence_router(Service()))
    response_obj = TestClient(app).get("/v1/model-intelligence/diagnostics/nvidia")
    assert response_obj.status_code == 200
    assert "no-store" in response_obj.headers["cache-control"]
    assert response_obj.json()["admission_ready"] is True
